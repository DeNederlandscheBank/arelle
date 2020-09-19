from setuptools import setup

setup(
  name = 'dnb-arelle',
  packages = ['arelle'], 
  package_data = {'arelle': ['arelle/*', 'pyparsing/*']},
  version = '0.1.0',
  description = 'Arelle modified by DeNederlandscheBank',
  author="De Nederlandsche Bank",
  author_email='ECDB_berichten@dnb.nl',
  python_requires='>=3.0, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*, !=3.4.*',
  classifiers=['Development Status :: 2 - Pre-Alpha',
      		   'Intended Audience :: Financial and Insurance Industry',
      		   'Intended Audience :: Developers',
      		   'Intended Audience :: Education',
	  		   'License :: OSI Approved :: MIT License',
    		   'Programming Language :: Python :: 3.6'],
  url = 'https://github.com/DeNederlandscheBank/Arelle', 
  download_url = 'https://github.com/DeNederlandscheBank/Arelle/dist/arelle-0.1.0.tar.gz',
  keywords = ['arelle'], # arbitrary keywords
)