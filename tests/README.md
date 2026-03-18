# Tests

## Structure

```
tests/
├── unit/           ← Unit tests (pytest, CI)
├── integration/    ← Integration tests
└── README.md
```

## Running Tests

```bash
# Unit tests
pip install pytest
pytest tests/unit/ -v

# Built-in tests
python tools/run_tests.py

# Integration tests (requires network)
python tests/integration/test_template_matching.py
python tests/integration/test_real_projects.py
```

## Notes

- Unit tests run in CI across Python 3.10/3.12/3.13 on Ubuntu, macOS, Windows
- Integration tests only do `fetch` and `plan` — they never execute installs
- All network requests are read-only GitHub API calls
- No LLM API keys required for testing
