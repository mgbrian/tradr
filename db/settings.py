import os
try:
    import env

except:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')

DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
    },
    # TODO: Update to this once pg is set up.
    # 'default': {
    #     'ENGINE': 'django.db.backends.postgresql_psycopg2',
    #     'NAME': os.environ.get('POSTGRES_DB_NAME'),
    #     'USER': os.environ.get('POSTGRES_DB_USER'),
    #     'PASSWORD': os.environ.get('POSTGRES_DB_PASSWORD'),
    #     'HOST': os.environ.get('POSTGRES_HOST'),
    #     'PORT': os.environ.get('POSTGRES_PORT'),
    # }
}

INSTALLED_APPS = (
    'db',
)
