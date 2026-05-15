# PythonAnywhere Deployment Guide

## Prerequisites
- PythonAnywhere account (free or paid)
- Git repository with your project (or upload via files)

## Step 1: Upload Your Project

### Option A: Via Git (Recommended)
1. Push your code to GitHub/GitLab
2. Open PythonAnywhere Bash console
3. Run:
```bash
git clone https://github.com/yourusername/best-inventory-system.git
cd best-inventory-system
```

### Option B: Via File Upload
1. Upload all project files via PythonAnywhere Files tab
2. Place them in `/home/yourusername/best-inventory-system/`

## Step 2: Create Virtual Environment

In PythonAnywhere Bash console:
```bash
cd /home/yourusername/best-inventory-system
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Note: PythonAnywhere supports Python 3.8, 3.9, 3.10, 3.11. Use whichever is available.

## Step 3: Configure Web App

1. Go to PythonAnywhere **Web** tab
2. Click **Add a new web app**
3. Select **Manual configuration** (not Django wizard)
4. Select Python version matching your venv (e.g., Python 3.10)

## Step 4: Configure WSGI File

1. Click the **WSGI configuration file** link in the Web tab
2. Replace ALL content with the content from `pythonanywhere_wsgi.py`
3. Update `yourusername` to your actual PythonAnywhere username
4. Save

## Step 5: Set Virtual Environment

1. In the Web tab, find **Virtualenv** section
2. Enter: `/home/yourusername/best-inventory-system/venv`

## Step 6: Set Environment Variables

In the Web tab, add these to **Environment variables**:

```
DJANGO_SECRET_KEY=your-secret-key-here-generate-one
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=yourusername.pythonanywhere.com
```

Generate a secret key with:
```python
python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

## Step 7: Configure Static Files

In the Web tab, add static files mapping:

| URL | Directory |
|-----|-----------|
| `/static/` | `/home/yourusername/best-inventory-system/staticfiles/` |
| `/media/` | `/home/yourusername/best-inventory-system/media/` |

Then run in Bash:
```bash
cd /home/yourusername/best-inventory-system
source venv/bin/activate
python manage.py collectstatic
```

## Step 8: Run Migrations

In Bash console:
```bash
cd /home/yourusername/best-inventory-system
source venv/bin/activate
python manage.py migrate
```

## Step 9: Create Superuser

```bash
python manage.py createsuperuser
```

## Step 10: Reload Web App

Click the green **Reload** button in the Web tab.

## Post-Deployment Checklist

- [ ] Visit your site URL and verify it loads
- [ ] Log in to admin at `/admin/`
- [ ] Create initial Shop entries (Warehouse + your shops)
- [ ] Create user accounts and assign shops via admin
- [ ] Test all major features: dashboard, POS, reports
- [ ] Set `DJANGO_DEBUG=False` in production

## Updating Your App

When you make changes:
```bash
cd /home/yourusername/best-inventory-system
source venv/bin/activate
git pull  # if using git
python manage.py collectstatic --noinput
python manage.py migrate
```
Then click **Reload** in the Web tab.

## Troubleshooting

### 500 Internal Server Error
- Check error log in PythonAnywhere Web tab
- Verify `DJANGO_ALLOWED_HOSTS` includes your domain
- Ensure `collectstatic` has been run

### Static files not loading
- Verify static files mapping in Web tab
- Run `python manage.py collectstatic` again

### Database locked errors
- SQLite is fine for small sites
- For high traffic, consider upgrading to MySQL (PythonAnywhere paid feature)
