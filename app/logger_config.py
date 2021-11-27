import os


def get_config():
  """Returns the logger configuration."""
  log_level = os.environ.get("LOG_LEVEL", "INFO")
  # TODO: do not log to file if this is empty.
  log_path = os.environ.get("LOG_PATH", "logging/hn_app.log")
  max_bytes = int(os.environ.get("LOG_MAX_BYTES", 200_000))
  backup_count = int(os.environ.get("LOG_BACKUP_COUNT", 1))

  return {
      'version': 1,
      'formatters': {'default': {
          'format': '[%(asctime)s] {%(funcName)s:%(lineno)d} %(levelname)s - %(message)s',
      }},
      'handlers': {
          'default': {
              'level': log_level,
              'formatter': 'default',
              'class': 'logging.handlers.RotatingFileHandler',
              'filename': log_path,
              'maxBytes': max_bytes,
              'backupCount': backup_count
          },
      },
      'root': {
          'level': log_level,
          'handlers': ['default']
      },
  }
