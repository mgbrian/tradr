# Credit: https://github.com/dancaron/Django-ORM/blob/master/settings.py

# Turn off bytecode generation
import sys
sys.dont_write_bytecode = True

# Django specific settings
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'db.settings')
import django
django.setup()
