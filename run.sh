#!/bin/bash

export PATH=/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin

cd /Users/jocelynlee/Desktop/projects/bookFinder || exit 1

PYTHON=/Users/jocelynlee/Desktop/projects/bookFinder/venv/bin/python
LOG=/Users/jocelynlee/Desktop/projects/bookFinder/cron.log

echo "=== $(date) ===" >> $LOG
$PYTHON ingest_library.py >> $LOG 2>&1
$PYTHON ingest_bookoutlet.py >> $LOG 2>&1