# Contributing to MLSTudio

> The project is in pre-alpha; the API and CLI will change without notice until v0.1.

## Dev setup

```bash
git clone git@github.com:iowa69/mlstudio.git
cd mlstudio
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Before opening a PR

```bash
ruff check --fix src tests
mypy src           # type-clean is the goal once stubs are real
pytest --cov
```

## Areas where help is welcome

- PubMLST API client (M1)
- cgMLST.org downloader (M1)
- Benchmark datasets / ground-truth labels (M10)
- Cytoscape.js styling — getting the visual polish right (M7)
- Conda recipe / packaging (M9)

## Communication

Open an issue before starting non-trivial work, so we don't duplicate effort.
