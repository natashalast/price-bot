#!/bin/bash

# Установка Tesseract на сервере
apt-get update
apt-get install -y tesseract-ocr tesseract-ocr-rus

# Запуск бота
python bot.py