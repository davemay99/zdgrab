from setuptools import setup

setup(name="zdgrab",
      version="3.0.0",
      scripts=["bin/zdgrab"],
      packages=["zdgrab"],
      description="Get attachments from Zendesk tickets.",
      long_description="Get attachments from Zendesk tickets.",
      classifiers=["Development Status :: 4 - Beta",
                   "Intended Audience :: End Users/Desktop",
                   "License :: OSI Approved :: Apache Software License",
                   "Topic :: Utilities",
                   ],
      keywords="zendesk attachment",
      author="Brent Woodruff",
      author_email="brent@fprimex.com",
      url="http://github.com/fprimex/zdgrab",
      license="Apache",
      include_package_data=True,
      zip_safe=False,
      install_requires=[
          "zdesk",
          "zdeskcfg",
      ],
      )
