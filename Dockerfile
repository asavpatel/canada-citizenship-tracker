# Use the official Python 3.12 Alpine image
FROM python:3.12-alpine

# Set the working directory to /app
WORKDIR /canada-citizenship-tracker

# Copy the requirements file
COPY requirements.txt .

# Install the dependencies
RUN pip install -r requirements.txt

# Copy the application code
COPY . .

# Run the command to start the application
CMD ["python", "citizenship_application_tracker.py"]