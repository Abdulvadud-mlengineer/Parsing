```md
# Anime/Web Scraper

Python-парсер для автоматического сбора данных с сайта.

## Возможности

- Парсинг данных
- Сохранение в CSV и PostgreSQL
- Автоматический сбор информации

## Stack

- Python
- BeautifulSoup
- Pandas
- PostgreSQL

## Run

```bash
pip install -r requirements.txt
python main.py

Я сделал парсер на Python для сайта animego.me.
Он автоматически собирает данные об аниме: название, жанры, рейтинг, эпизоды и актеров озвучки.
Проблема была в том, что сайт защищён Cloudflare и работает через JavaScript, поэтому обычный requests не подходил. Я использовал Playwright — он открывает сайт как настоящий браузер.
Дальше через BeautifulSoup парсил HTML и сохранял всё в PostgreSQL через SQLAlchemy.
Проект разделил на 3 части: база данных, логика парсинга и основной pipeline.
Также сделал retry при ошибках и автоматическое обновление данных через cron.»

Если чуть подробнее:

Playwright → чтобы обходить Cloudflare и JS.
BeautifulSoup + lxml → чтобы вытаскивать данные из HTML.
PostgreSQL → хранение данных.
SQLAlchemy → работа с БД через Python.
Tenacity → retry при ошибках сети.
Cron → автоматический запуск.
Full mode → полный сбор.
Incremental mode → обновляет только новые данные.

