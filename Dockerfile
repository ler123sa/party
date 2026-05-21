FROM python:3.11-slim

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем приложение
COPY . .

# Создаем директорию для базы данных
RUN mkdir -p /app/data

# Открываем порт
EXPOSE 5000

# Запускаем приложение
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
