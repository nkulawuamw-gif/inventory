# Stock Management System

A Django-based inventory management system for tracking stock across 5 shops in Malawi.

## Shops
- Zomba Shop
- Blantyre Shop
- Limbe Shop
- Lilongwe Shop
- Mzuzu Shop

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Migrations
```bash
python manage.py migrate
```

### 3. Create Superuser (for admin access)
```bash
python manage.py createsuperuser
```

### 4. Load Sample Data
```bash
python manage.py load_sample_data
```

### 5. Run Server
```bash
python manage.py runserver
```

Visit http://localhost:8000

## URLs

| URL | Description |
|-----|-------------|
| `/` | Main inventory dashboard |
| `/search/?q=sugar` | Search items across all shops |
| `/zomba/` | Zomba shop inventory |
| `/blantyre/` | Blantyre shop inventory |
| `/limbe/` | Limbe shop inventory |
| `/lilongwe/` | Lilongwe shop inventory |
| `/mzuzu/` | Mzuzu shop inventory |
| `/export-csv/` | Export inventory to CSV |
| `/admin/` | Django admin panel |

## Features
- Global inventory dashboard with aggregated stock data
- Individual shop dashboards
- Cross-shop search with partial matching
- Low stock alerts (highlighted in red)
- Filter by shop dropdown
- CSV export
- Django admin for managing shops and items
- Bootstrap 5 responsive UI
