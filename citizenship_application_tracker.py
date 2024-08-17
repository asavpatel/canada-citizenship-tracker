import json
import logging
from datetime import datetime
import time

import requests
import schedule
import sendgrid
from jinja2 import Environment, FileSystemLoader
from sendgrid.helpers.mail import Mail

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
with open('config.json', 'r') as f:
    config = json.load(f)

# Constants
AUTH_URL = 'https://cognito-idp.ca-central-1.amazonaws.com/'
PROFILE_URL = 'https://api.tracker-suivi.apps.cic.gc.ca/user'
CLIENT_ID = 'mtnf1qn9p739g2v8aij2anpju'
SENDGRID_API_KEY = config['sendgrid_api_key']
EMAIL_SENDER = config['sender_email']

user_profiles = config['user_profiles']
schedule_time = int(config['schedule_time_mins'])


# Fetch the access token
def get_access_token(username, password):
    try:
        response = requests.post(
            AUTH_URL,
            headers={
                'accept': '*/*',
                'content-type': 'application/x-amz-json-1.1',
                'x-amz-target': 'AWSCognitoIdentityProviderService.InitiateAuth'
            },
            json={
                'AuthFlow': 'USER_PASSWORD_AUTH',
                'ClientId': CLIENT_ID,
                'AuthParameters': {
                    'USERNAME': username,
                    'PASSWORD': password
                }
            }
        )
        response.raise_for_status()
        response_data = response.json()
        return response_data['AuthenticationResult']['IdToken']
    except requests.RequestException as e:
        logging.error(f"Error fetching access token for user {username}: {e}")
        return None


# Fetch the profile summary to get the application number (appNumber)
def get_profile_summary(access_token):
    try:
        response = requests.post(
            PROFILE_URL,
            headers={
                'accept': 'application/json',
                'authorization': f'Bearer {access_token}',
                'content-type': 'application/json'
            },
            json={
                'method': 'get-profile-summary',
                'limit': '500'
            }
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logging.error(f"Error fetching profile summary: {e}")
        return None


# Fetch application status using the dynamic appNumber
def get_application_status(access_token, app_number):
    try:
        response = requests.post(
            PROFILE_URL,
            headers={
                'accept': 'application/json',
                'authorization': f'Bearer {access_token}',
                'content-type': 'application/json'
            },
            json={
                'method': 'get-application-details',
                'applicationNumber': app_number
            }
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logging.error(f"Error fetching application status for {app_number}: {e}")
        return None


# Initialize Jinja2 environment
env = Environment(loader=FileSystemLoader('.'))  # Template files are in the current directory


# Generate HTML email content using Jinja2
def generate_html_content(template_file, current_data, previous_data, changes):
    # Prepare the activities list with previous and current status
    previous_activities = {activity['activity']: activity['status'] for activity in previous_data.get('activities', ())}
    activities = [
        {
            'activity': activity['activity'],
            'previous_status': previous_activities.get(activity['activity'], 'N/A'),
            'current_status': activity['status']
        }
        for activity in current_data['activities']
    ]

    # Prepare the history list with 'is_new' flag to mark new entries
    previous_history = {item['time']: item for item in previous_data.get('history', [])} if previous_data else {}
    history = [
        {
            'time': datetime.fromtimestamp(item['time'] / 1000).strftime('%Y-%m-%d %H:%M:%S'),
            'title': item['title']['en'],
            'details': item['text']['en'],
            'is_new': item['time'] not in previous_history  # Mark new history entries
        }
        for item in current_data['history']
    ]

    # Load the Jinja2 template
    template = env.get_template(template_file)

    # Render the template with the dynamic values
    html_content = template.render(
        application_number=current_data['applicationNumber'],
        status=current_data['status'],
        last_updated_time=datetime.fromtimestamp(current_data['lastUpdatedTime'] / 1000),
        activities=activities,
        history=history,
        changes=changes
    )

    return html_content


# Send an email notification via SendGrid
def send_email(subject, body, to_email):
    sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
    mail = Mail(
        from_email=EMAIL_SENDER,
        to_emails=to_email,
        subject=subject,
        html_content=body)
    try:
        response = sg.send(mail)
        logging.info(f"Email sent to {to_email} with status code {response.status_code}")
    except Exception as e:
        logging.error(f"Error sending email to {to_email}: {e}")


# Track status changes using lastUpdatedTime and other data
def track_status_changes(user_profiles):
    template_file = 'email_template.html'

    for profile in user_profiles:
        username, password, receiver_email = profile['username'], profile['password'], profile['receiver_email']
        access_token = get_access_token(username, password)
        if not access_token:
            continue

        profile_summary = get_profile_summary(access_token)
        if not profile_summary or 'apps' not in profile_summary or not profile_summary['apps']:
            logging.warn(f"No applications found for {username}.")
            continue

        app_number = profile_summary['apps'][0]['appNumber']
        current_data = get_application_status(access_token, app_number)
        if not current_data:
            continue

        # Compare current lastUpdatedTime with stored data
        try:
            with open(f'status_{username}_{app_number}.json', 'r') as f:
                previous_data = json.load(f)
                last_updated_time = previous_data.get('lastUpdatedTime')
        except FileNotFoundError:
            previous_data = {}
            last_updated_time = None

        # Check if the application has been updated since last check
        if last_updated_time is None or current_data['lastUpdatedTime'] != last_updated_time:
            changes = []

            # Track any relevant changes
            if last_updated_time:
                changes.append(
                    f"Application {app_number} updated at {datetime.fromtimestamp(current_data['lastUpdatedTime'] / 1000)}")

            # Save new data to file
            with open(f'status_{username}_{app_number}.json', 'w') as f:
                json.dump(current_data, f)

            # Generate HTML email
            html_content = generate_html_content(template_file, current_data, previous_data, changes)

            # Send email notification
            send_email(
                'Citizenship Tracker - Application Status Change Notification',
                html_content,
                receiver_email
            )
        else:
            logging.info(f"No changes for {username}, Application {app_number}.")


def job():
    logging.info(f'running now: {datetime.now()}')
    track_status_changes(user_profiles)
    logging.info('finished running')


schedule.every(schedule_time).minutes.do(job)

if __name__ == '__main__':
    job()
    while True:
        schedule.run_pending()
        time.sleep(schedule_time * 60)
