#!/usr/bin/bash
# This is a Beanstalk pipservice deployment script.
# Beanstalk Python apps are deployed to /var/app/current
cd /var/app/current/
# Install poetry
python3.13 -m pip install --upgrade poetry
# Install dependencies for the service if we find a pyproject.toml file
if test -f pyproject.toml; then
    poetry install --no-interaction --no-root
fi
# For each .service file we find here, install it and (re)start the service
for file in *.service ; do
  cp "$file" /lib/systemd/system/
  systemctl daemon-reload
  systemctl restart "$file"
done
