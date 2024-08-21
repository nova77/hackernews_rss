import logging
import os
import sys


def get_logger():
  loglevel = os.environ.get('LOG_LEVEL', 'INFO').upper()
  logformat = "[%(levelname)s|%(asctime)s|%(name)s:%(lineno)s] %(message)s"

  logging.basicConfig(
      stream=sys.stdout, level=loglevel,
      format=logformat)

  return logging.getLogger(os.path.basename(__file__))
