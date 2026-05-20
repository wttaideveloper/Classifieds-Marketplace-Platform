# Classifieds Marketplace Platform

## Local setup

1. Activate your Python virtual environment.
2. Install dependencies from `requirements.txt`.
3. Create the database tables before running the app:

```powershell
python -m scripts.create_tables
```

4. Start the app:

```powershell
uvicorn app.main:app --reload
```

If you want automatic table creation on startup instead, set:

```powershell
$env:AUTO_CREATE_TABLES = "true"
uvicorn app.main:app --reload
```
