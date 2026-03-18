"""
enterprise.py - 企业功能模块
================================

企业级功能：
  1. SSO 集成 (SAML 2.0 / OIDC)
  2. RBAC 角色权限管理
  3. 审计日志
  4. 私有仓库访问管理
  5. 合规报告导出

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ─────────────────────────────────────────────
#  RBAC（基于角色的访问控制）
# ─────────────────────────────────────────────

class Permission(Enum):
    """系统权限枚举"""
    # 安装操作
    INSTALL_PUBLIC = "install:public"         # 安装公开仓库项目
    INSTALL_PRIVATE = "install:private"       # 安装私有仓库项目
    INSTALL_UNTRUSTED = "install:untrusted"   # 安装未经审核的项目
    # 审计
    AUDIT_READ = "audit:read"                 # 查看审计日志
    AUDIT_EXPORT = "audit:export"             # 导出审计报告
    # 管理
    ADMIN_USERS = "admin:users"               # 管理用户
    ADMIN_ROLES = "admin:roles"               # 管理角色
    ADMIN_SETTINGS = "admin:settings"         # 修改系统设置
    ADMIN_REPOS = "admin:repos"               # 管理仓库白名单
    # SBOM
    SBOM_GENERATE = "sbom:generate"           # 生成 SBOM
    SBOM_EXPORT = "sbom:export"               # 导出 SBOM
    # 高级
    BATCH_INSTALL = "batch:install"           # 批量安装
    API_ACCESS = "api:access"                 # API 访问


# 预定义角色
BUILTIN_ROLES: dict[str, dict] = {
    "viewer": {
        "name": "查看者",
        "description": "只读权限，可查看项目信息和审计日志",
        "permissions": [
            Permission.INSTALL_PUBLIC.value,
            Permission.AUDIT_READ.value,
        ],
    },
    "developer": {
        "name": "开发者",
        "description": "可安装公开和私有仓库项目",
        "permissions": [
            Permission.INSTALL_PUBLIC.value,
            Permission.INSTALL_PRIVATE.value,
            Permission.AUDIT_READ.value,
            Permission.SBOM_GENERATE.value,
            Permission.API_ACCESS.value,
        ],
    },
    "security": {
        "name": "安全工程师",
        "description": "安全审计和 SBOM 管理权限",
        "permissions": [
            Permission.INSTALL_PUBLIC.value,
            Permission.INSTALL_PRIVATE.value,
            Permission.AUDIT_READ.value,
            Permission.AUDIT_EXPORT.value,
            Permission.SBOM_GENERATE.value,
            Permission.SBOM_EXPORT.value,
        ],
    },
    "admin": {
        "name": "管理员",
        "description": "完全管理权限",
        "permissions": [p.value for p in Permission],
    },
}


@dataclass
class User:
    """企业用户"""
    user_id: str
    username: str
    email: str
    roles: list[str] = field(default_factory=lambda: ["developer"])
    is_active: bool = True
    sso_provider: str = ""       # "saml" / "oidc" / "local"
    sso_subject: str = ""        # SSO 身份标识
    created_at: float = field(default_factory=time.time)
    last_login: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class AuditEntry:
    """审计日志条目"""
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    username: str = ""
    action: str = ""             # install, audit, sbom_export, user_create, ...
    resource: str = ""           # 操作目标（仓库 URL、用户 ID 等）
    result: str = "success"      # success / denied / error
    details: dict = field(default_factory=dict)
    ip_address: str = ""
    user_agent: str = ""


class RBACManager:
    """RBAC 角色权限管理器"""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = config_dir or os.path.expanduser("~/.gitinstall/enterprise")
        self._users: dict[str, User] = {}
        self._custom_roles: dict[str, dict] = {}
        self._repo_whitelist: set[str] = set()
        self._repo_blacklist: set[str] = set()
        self._load_config()

    def _config_path(self, name: str) -> str:
        return os.path.join(self._config_dir, name)

    def _load_config(self) -> None:
        """加载持久化配置"""
        if not os.path.isdir(self._config_dir):
            return

        # 加载用户
        users_path = self._config_path("users.json")
        if os.path.isfile(users_path):
            with open(users_path, encoding="utf-8") as f:
                data = json.load(f)
            for u in data.get("users", []):
                user = User(**{k: v for k, v in u.items() if k in User.__dataclass_fields__})
                self._users[user.user_id] = user

        # 加载自定义角色
        roles_path = self._config_path("roles.json")
        if os.path.isfile(roles_path):
            with open(roles_path, encoding="utf-8") as f:
                self._custom_roles = json.load(f)

        # 加载仓库白/黑名单
        repos_path = self._config_path("repos.json")
        if os.path.isfile(repos_path):
            with open(repos_path, encoding="utf-8") as f:
                data = json.load(f)
            self._repo_whitelist = set(data.get("whitelist", []))
            self._repo_blacklist = set(data.get("blacklist", []))

    def _save_users(self) -> None:
        os.makedirs(self._config_dir, exist_ok=True)
        data = {"users": []}
        for u in self._users.values():
            data["users"].append({
                "user_id": u.user_id, "username": u.username,
                "email": u.email, "roles": u.roles,
                "is_active": u.is_active, "sso_provider": u.sso_provider,
                "sso_subject": u.sso_subject,
                "created_at": u.created_at, "last_login": u.last_login,
            })
        with open(self._config_path("users.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _save_roles(self) -> None:
        os.makedirs(self._config_dir, exist_ok=True)
        with open(self._config_path("roles.json"), "w", encoding="utf-8") as f:
            json.dump(self._custom_roles, f, indent=2, ensure_ascii=False)

    def _save_repos(self) -> None:
        os.makedirs(self._config_dir, exist_ok=True)
        with open(self._config_path("repos.json"), "w", encoding="utf-8") as f:
            json.dump({
                "whitelist": sorted(self._repo_whitelist),
                "blacklist": sorted(self._repo_blacklist),
            }, f, indent=2, ensure_ascii=False)

    # ── 用户管理 ──

    def create_user(
        self, username: str, email: str,
        roles: Optional[list[str]] = None,
        sso_provider: str = "local",
        sso_subject: str = "",
    ) -> User:
        user_id = str(uuid.uuid4())
        user = User(
            user_id=user_id, username=username, email=email,
            roles=roles or ["developer"],
            sso_provider=sso_provider, sso_subject=sso_subject,
        )
        self._users[user_id] = user
        self._save_users()
        return user

    def get_user(self, user_id: str) -> Optional[User]:
        return self._users.get(user_id)

    def find_user_by_email(self, email: str) -> Optional[User]:
        for u in self._users.values():
            if u.email == email:
                return u
        return None

    def find_user_by_sso(self, provider: str, subject: str) -> Optional[User]:
        for u in self._users.values():
            if u.sso_provider == provider and u.sso_subject == subject:
                return u
        return None

    def list_users(self) -> list[User]:
        return list(self._users.values())

    def update_user_roles(self, user_id: str, roles: list[str]) -> bool:
        user = self._users.get(user_id)
        if not user:
            return False
        user.roles = roles
        self._save_users()
        return True

    def deactivate_user(self, user_id: str) -> bool:
        user = self._users.get(user_id)
        if not user:
            return False
        user.is_active = False
        self._save_users()
        return True

    # ── 权限检查 ──

    def get_role(self, role_name: str) -> Optional[dict]:
        if role_name in BUILTIN_ROLES:
            return BUILTIN_ROLES[role_name]
        return self._custom_roles.get(role_name)

    def get_user_permissions(self, user_id: str) -> set[str]:
        user = self._users.get(user_id)
        if not user or not user.is_active:
            return set()
        perms = set()
        for role_name in user.roles:
            role = self.get_role(role_name)
            if role:
                perms.update(role.get("permissions", []))
        return perms

    def check_permission(self, user_id: str, permission: str) -> bool:
        return permission in self.get_user_permissions(user_id)

    def check_install_access(self, user_id: str, repo_url: str) -> dict:
        """检查用户是否有权限安装指定仓库"""
        user = self._users.get(user_id)
        if not user or not user.is_active:
            return {"allowed": False, "reason": "用户不存在或已停用"}

        perms = self.get_user_permissions(user_id)

        # 检查黑名单
        normalized = self._normalize_repo(repo_url)
        if normalized in self._repo_blacklist:
            return {"allowed": False, "reason": f"仓库 {normalized} 在黑名单中"}

        # 白名单模式：如果白名单非空，只允许白名单中的仓库
        if self._repo_whitelist and normalized not in self._repo_whitelist:
            if Permission.INSTALL_UNTRUSTED.value not in perms:
                return {"allowed": False, "reason": f"仓库 {normalized} 不在白名单中"}

        # 私有仓库判断（简单启发式）
        is_private = "private" in repo_url or "internal" in repo_url
        if is_private and Permission.INSTALL_PRIVATE.value not in perms:
            return {"allowed": False, "reason": "无私有仓库安装权限"}

        if Permission.INSTALL_PUBLIC.value not in perms:
            return {"allowed": False, "reason": "无安装权限"}

        return {"allowed": True, "reason": ""}

    @staticmethod
    def _normalize_repo(url: str) -> str:
        """标准化仓库 URL: owner/repo"""
        match = re.search(r'(?:github\.com|gitlab\.com)[/:]([^/]+/[^/.]+)', url)
        if match:
            return match.group(1).lower()
        return url.lower().strip("/")

    # ── 自定义角色 ──

    def create_role(self, name: str, display_name: str,
                    description: str, permissions: list[str]) -> dict:
        # 验证权限值
        valid = {p.value for p in Permission}
        invalid = set(permissions) - valid
        if invalid:
            raise ValueError(f"无效的权限: {invalid}")

        role = {
            "name": display_name,
            "description": description,
            "permissions": permissions,
        }
        self._custom_roles[name] = role
        self._save_roles()
        return role

    # ── 仓库管理 ──

    def add_to_whitelist(self, repo: str) -> None:
        self._repo_whitelist.add(self._normalize_repo(repo))
        self._save_repos()

    def add_to_blacklist(self, repo: str) -> None:
        self._repo_blacklist.add(self._normalize_repo(repo))
        self._save_repos()

    def remove_from_whitelist(self, repo: str) -> None:
        self._repo_whitelist.discard(self._normalize_repo(repo))
        self._save_repos()

    def remove_from_blacklist(self, repo: str) -> None:
        self._repo_blacklist.discard(self._normalize_repo(repo))
        self._save_repos()


# ─────────────────────────────────────────────
#  审计日志管理
# ─────────────────────────────────────────────

class AuditLogger:
    """审计日志管理器，支持 JSON Lines 持久化"""

    def __init__(self, log_dir: Optional[str] = None):
        self._log_dir = log_dir or os.path.expanduser("~/.gitinstall/enterprise/audit")
        os.makedirs(self._log_dir, exist_ok=True)

    def _log_file(self) -> str:
        """按日切分日志文件"""
        date_str = time.strftime("%Y-%m-%d")
        return os.path.join(self._log_dir, f"audit-{date_str}.jsonl")

    def log(self, entry: AuditEntry) -> None:
        """写入审计日志"""
        record = {
            "timestamp": entry.timestamp,
            "event_id": entry.event_id,
            "user_id": entry.user_id,
            "username": entry.username,
            "action": entry.action,
            "resource": entry.resource,
            "result": entry.result,
            "details": entry.details,
            "ip_address": entry.ip_address,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self._log_file(), "a", encoding="utf-8") as f:
            f.write(line)

    def log_install(self, user: User, repo_url: str,
                    result: str = "success", details: Optional[dict] = None) -> None:
        self.log(AuditEntry(
            user_id=user.user_id, username=user.username,
            action="install", resource=repo_url,
            result=result, details=details or {},
        ))

    def log_permission_denied(self, user: User, action: str, resource: str) -> None:
        self.log(AuditEntry(
            user_id=user.user_id, username=user.username,
            action=action, resource=resource, result="denied",
        ))

    def query(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """查询审计日志"""
        results = []
        log_files = sorted(
            [f for f in os.listdir(self._log_dir) if f.startswith("audit-") and f.endswith(".jsonl")],
            reverse=True,
        )

        for log_file in log_files:
            filepath = os.path.join(self._log_dir, log_file)
            with open(filepath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    ts = record.get("timestamp", 0)
                    if start_time and ts < start_time:
                        continue
                    if end_time and ts > end_time:
                        continue
                    if user_id and record.get("user_id") != user_id:
                        continue
                    if action and record.get("action") != action:
                        continue
                    results.append(record)
                    if len(results) >= limit:
                        return results
        return results

    def export_compliance_report(
        self,
        start_time: float,
        end_time: float,
        output_path: Optional[str] = None,
    ) -> str:
        """导出合规审计报告"""
        entries = self.query(start_time=start_time, end_time=end_time, limit=10000)

        # 统计
        action_counts: dict[str, int] = {}
        denied_count = 0
        user_activity: dict[str, int] = {}

        for entry in entries:
            act = entry.get("action", "unknown")
            action_counts[act] = action_counts.get(act, 0) + 1
            if entry.get("result") == "denied":
                denied_count += 1
            uid = entry.get("username", "unknown")
            user_activity[uid] = user_activity.get(uid, 0) + 1

        report = {
            "report_type": "compliance_audit",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "period": {
                "start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start_time)),
                "end": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end_time)),
            },
            "summary": {
                "total_events": len(entries),
                "denied_events": denied_count,
                "unique_users": len(user_activity),
                "action_breakdown": action_counts,
            },
            "top_users": sorted(user_activity.items(), key=lambda x: x[1], reverse=True)[:20],
            "denied_events": [e for e in entries if e.get("result") == "denied"][:50],
            "entries": entries,
        }

        if not output_path:
            output_path = os.path.join(
                self._log_dir,
                f"compliance-report-{time.strftime('%Y%m%d%H%M%S')}.json",
            )

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        return output_path


# ─────────────────────────────────────────────
#  SSO 集成 (SAML 2.0 / OIDC)
# ─────────────────────────────────────────────

@dataclass
class SSOConfig:
    """SSO 配置"""
    provider: str = ""               # "saml" / "oidc"
    # OIDC
    oidc_issuer: str = ""            # https://accounts.google.com
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    oidc_scopes: list[str] = field(default_factory=lambda: ["openid", "email", "profile"])
    # SAML
    saml_idp_metadata_url: str = ""
    saml_entity_id: str = ""
    saml_acs_url: str = ""
    # 通用
    auto_create_user: bool = True    # SSO 登录时自动创建用户
    default_role: str = "developer"  # 新用户默认角色


class OIDCHandler:
    """
    OIDC (OpenID Connect) 认证处理器。

    支持 Google Workspace、Azure AD、Okta、Auth0 等标准 OIDC Provider。
    使用 Authorization Code Flow（最安全的 Web 流程）。
    """

    def __init__(self, config: SSOConfig):
        self.config = config
        self._well_known: Optional[dict] = None

    def _fetch_well_known(self) -> dict:
        """获取 OIDC Discovery 文档"""
        if self._well_known:
            return self._well_known
        url = f"{self.config.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "gitinstall/1.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            self._well_known = json.loads(resp.read().decode("utf-8"))
        return self._well_known

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """生成 OIDC 授权 URL"""
        wk = self._fetch_well_known()
        auth_endpoint = wk["authorization_endpoint"]
        if not state:
            state = secrets.token_urlsafe(32)
        params = {
            "response_type": "code",
            "client_id": self.config.oidc_client_id,
            "redirect_uri": self.config.oidc_redirect_uri,
            "scope": " ".join(self.config.oidc_scopes),
            "state": state,
        }
        query = "&".join(f"{k}={_url_encode(v)}" for k, v in params.items())
        return f"{auth_endpoint}?{query}"

    def exchange_code(self, code: str) -> dict:
        """用授权码换取 Token"""
        wk = self._fetch_well_known()
        token_endpoint = wk["token_endpoint"]

        payload = (
            f"grant_type=authorization_code"
            f"&code={_url_encode(code)}"
            f"&redirect_uri={_url_encode(self.config.oidc_redirect_uri)}"
            f"&client_id={_url_encode(self.config.oidc_client_id)}"
            f"&client_secret={_url_encode(self.config.oidc_client_secret)}"
        ).encode("utf-8")

        import urllib.request
        req = urllib.request.Request(
            token_endpoint, data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_userinfo(self, access_token: str) -> dict:
        """获取用户信息"""
        wk = self._fetch_well_known()
        userinfo_endpoint = wk.get("userinfo_endpoint", "")
        if not userinfo_endpoint:
            # 从 ID Token 解析
            return {}

        import urllib.request
        req = urllib.request.Request(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def authenticate(self, code: str, rbac: RBACManager) -> Optional[User]:
        """完整 OIDC 认证流程：code → token → userinfo → User"""
        tokens = self.exchange_code(code)
        access_token = tokens.get("access_token", "")
        if not access_token:
            return None

        userinfo = self.get_userinfo(access_token)
        email = userinfo.get("email", "")
        subject = userinfo.get("sub", "")
        name = userinfo.get("name", email.split("@")[0] if email else "unknown")

        if not email and not subject:
            return None

        # 查找已有用户
        user = rbac.find_user_by_sso("oidc", subject)
        if not user and email:
            user = rbac.find_user_by_email(email)

        # 自动创建
        if not user and self.config.auto_create_user:
            user = rbac.create_user(
                username=name, email=email,
                roles=[self.config.default_role],
                sso_provider="oidc", sso_subject=subject,
            )

        if user:
            user.last_login = time.time()

        return user


def _url_encode(s: str) -> str:
    """最小化URL编码"""
    import urllib.parse
    return urllib.parse.quote(s, safe="")


# ─────────────────────────────────────────────
#  私有仓库访问管理
# ─────────────────────────────────────────────

@dataclass
class PrivateRepoCredential:
    """私有仓库凭据"""
    repo_pattern: str       # glob 模式，如 "company/*", "github.com/org/*"
    auth_type: str          # "token" / "ssh" / "app"
    token: str = ""         # GitHub PAT / GitLab Token
    ssh_key_path: str = ""  # SSH 私钥路径
    app_id: str = ""        # GitHub App ID
    app_private_key: str = "" # GitHub App 私钥路径
    expires_at: float = 0.0   # Token 过期时间


class PrivateRepoManager:
    """私有仓库凭据管理器"""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = config_dir or os.path.expanduser("~/.gitinstall/enterprise")
        self._credentials: list[PrivateRepoCredential] = []
        self._load()

    def _creds_path(self) -> str:
        return os.path.join(self._config_dir, "private_repos.json")

    def _load(self) -> None:
        path = self._creds_path()
        if not os.path.isfile(path):
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for c in data.get("credentials", []):
            self._credentials.append(PrivateRepoCredential(
                repo_pattern=c["repo_pattern"],
                auth_type=c["auth_type"],
                token=c.get("token", ""),
                ssh_key_path=c.get("ssh_key_path", ""),
                app_id=c.get("app_id", ""),
                expires_at=c.get("expires_at", 0),
            ))

    def _save(self) -> None:
        os.makedirs(self._config_dir, exist_ok=True)
        data = {"credentials": []}
        for c in self._credentials:
            data["credentials"].append({
                "repo_pattern": c.repo_pattern,
                "auth_type": c.auth_type,
                "token": "***" if c.token else "",  # 不明文存储
                "ssh_key_path": c.ssh_key_path,
                "app_id": c.app_id,
                "expires_at": c.expires_at,
            })
        with open(self._creds_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add_credential(self, cred: PrivateRepoCredential) -> None:
        self._credentials.append(cred)
        self._save()

    def find_credential(self, repo_url: str) -> Optional[PrivateRepoCredential]:
        """查找匹配的凭据"""
        import fnmatch
        normalized = _normalize_repo_url(repo_url)
        for cred in self._credentials:
            if fnmatch.fnmatch(normalized, cred.repo_pattern):
                if cred.expires_at and cred.expires_at < time.time():
                    continue  # 跳过过期凭据
                return cred
        return None

    def get_clone_env(self, repo_url: str) -> dict[str, str]:
        """获取克隆私有仓库所需的环境变量"""
        cred = self.find_credential(repo_url)
        if not cred:
            return {}
        env = {}
        if cred.auth_type == "token" and cred.token:
            # 使用 token 的 HTTPS clone
            env["GIT_ASKPASS"] = "echo"
            env["GIT_USERNAME"] = "x-access-token"
            env["GIT_PASSWORD"] = cred.token
        elif cred.auth_type == "ssh" and cred.ssh_key_path:
            env["GIT_SSH_COMMAND"] = f"ssh -i {cred.ssh_key_path} -o StrictHostKeyChecking=no"
        return env

    def get_authenticated_url(self, repo_url: str) -> str:
        """生成带认证信息的克隆 URL"""
        cred = self.find_credential(repo_url)
        if not cred:
            return repo_url
        if cred.auth_type == "token" and cred.token:
            # https://x-access-token:TOKEN@github.com/org/repo.git
            match = re.match(r'https?://([^/]+)/(.*)', repo_url)
            if match:
                host, path = match.group(1), match.group(2)
                return f"https://x-access-token:{cred.token}@{host}/{path}"
        elif cred.auth_type == "ssh":
            # 转为 SSH URL
            match = re.match(r'https?://([^/]+)/(.*)', repo_url)
            if match:
                host, path = match.group(1), match.group(2)
                return f"git@{host}:{path}"
        return repo_url


def _normalize_repo_url(url: str) -> str:
    """标准化仓库 URL"""
    url = re.sub(r'^https?://', '', url)
    url = url.rstrip("/").rstrip(".git")
    return url.lower()


# ─────────────────────────────────────────────
#  企业 API 端点（供 web.py 集成）
# ─────────────────────────────────────────────

def create_enterprise_api_routes() -> dict:
    """
    返回企业 API 路由定义，供 web.py 注册。

    Returns:
        {path: handler_info} 字典
    """
    return {
        "/api/enterprise/users": {
            "GET": "list_users",
            "POST": "create_user",
            "description": "用户管理",
        },
        "/api/enterprise/users/{user_id}": {
            "GET": "get_user",
            "PUT": "update_user",
            "DELETE": "deactivate_user",
            "description": "单用户操作",
        },
        "/api/enterprise/users/{user_id}/roles": {
            "PUT": "update_user_roles",
            "description": "更新用户角色",
        },
        "/api/enterprise/roles": {
            "GET": "list_roles",
            "POST": "create_role",
            "description": "角色管理",
        },
        "/api/enterprise/repos/whitelist": {
            "GET": "list_whitelist",
            "POST": "add_to_whitelist",
            "DELETE": "remove_from_whitelist",
            "description": "仓库白名单",
        },
        "/api/enterprise/repos/blacklist": {
            "GET": "list_blacklist",
            "POST": "add_to_blacklist",
            "DELETE": "remove_from_blacklist",
            "description": "仓库黑名单",
        },
        "/api/enterprise/audit": {
            "GET": "query_audit",
            "description": "审计日志查询",
        },
        "/api/enterprise/audit/export": {
            "POST": "export_audit_report",
            "description": "导出合规报告",
        },
        "/api/enterprise/sso/oidc/authorize": {
            "GET": "oidc_authorize",
            "description": "OIDC 授权重定向",
        },
        "/api/enterprise/sso/oidc/callback": {
            "GET": "oidc_callback",
            "description": "OIDC 回调处理",
        },
        "/api/enterprise/sbom/export": {
            "POST": "export_sbom",
            "description": "导出 SBOM",
        },
        "/api/enterprise/check-access": {
            "POST": "check_install_access",
            "description": "检查安装权限",
        },
    }
