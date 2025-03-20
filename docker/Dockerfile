# Use the official Python base image
FROM python:3
# Set environment variables to prevent Python from writing .pyc files and to ensure stdout/stderr are logged
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

# Set the working directory inside the container
WORKDIR /app

# Copy project files to container
COPY . /app/ 

# Install any required dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port 8000 for Django development server
EXPOSE 8000
