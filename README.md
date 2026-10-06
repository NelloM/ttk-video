# Django su Render: stampa di due valori

## Locale
```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
DEBUG=True python manage.py runserver
```
Apri http://127.0.0.1:8000, compila i campi e clicca Invia: i valori compaiono nel terminale.

## Deploy su Render
1. Carica il progetto su GitHub.
2. Render -> New -> Web Service (oppure Blueprint, che usa render.yaml).
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn config.wsgi:application`
5. Variabile d'ambiente `SECRET_KEY` con una stringa lunga e casuale (con render.yaml viene generata da sola).

L'output di `print` si vede nella tab Logs del servizio su Render.
