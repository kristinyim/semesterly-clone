DEBUG = True

TEMPLATE_DEBUG = DEBUG

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': 'my_semesterly',
        'USER': 'kyim',
        'PASSWORD': 'strawberry',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}