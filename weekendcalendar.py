import json
from collections import namedtuple
import datetime

import flask
import httplib2
import dateutil.relativedelta
import itertools

from oauth2client import client
from apiclient import discovery

app = flask.Flask(__name__)
app.secret_key = 'e4ed8e36-141f-440c-b587-b6b1e99b5b4b'


ignored_descriptions = {'check that rent was paid'}
EventTuple = namedtuple('EventTuple', ['start', 'end', 'description'])


def get_calendar_events(http, calendar_id):
    service = discovery.build('calendar', 'v3', http=http)
    now = datetime.datetime.utcnow()


    WEDNESDAY = 2
    today = datetime.date.today()
    diff = today.weekday() - WEDNESDAY
    if diff < 0:
        diff += 7
    first_day = today - datetime.timedelta(days=diff)
    last_day = first_day + datetime.timedelta(days=365)

    def format_time(date):
        return datetime.datetime.combine(date, datetime.datetime.min.time()).isoformat() + 'Z'
    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=format_time(first_day), timeMax=format_time(last_day),
        maxResults=2500).execute()
    events_raw = events_result['items']

    warnings = ''

    def parse_date_datetime(d):
        if 'date' in d:
            date_str = d['date']
            is_date = True
        else:
            date_str = d['dateTime'][:10]
            is_date = False
        return datetime.datetime.strptime(date_str, '%Y-%m-%d').date(), is_date

    def parse_event(event):
        start, _ = parse_date_datetime(event['start'])
        end, is_date = parse_date_datetime(event['end'])
        if is_date:
            end = end - datetime.timedelta(days=1)
        description = event['summary']

        if description in ignored_descriptions:
            raise StopIteration

        yield EventTuple(start, end, description)

        if 'recurrence' in event:
            delta = None
            if len(event['recurrence']) == 1:
                recurrence_string = event['recurrence'][0]
                if recurrence_string == 'RRULE:FREQ=YEARLY':
                    delta = dateutil.relativedelta.relativedelta(years=1)
                elif recurrence_string == 'RRULE:FREQ=MONTHLY':
                    delta = dateutil.relativedelta.relativedelta(months=1)

            if delta:
                while start < last_day:
                    start = start + delta
                    end = end + delta
                    yield EventTuple(start, end, description)
            else:
                warnings += 'WARNING:Unknown recurrence {} found, some events might be missing'.format(event['recurrence'])

    events = list(itertools.chain.from_iterable(parse_event(e) for e in events_raw))

    def events_for_week(r):
        return ', '.join([e.description for e in events if max(e.start, r[0]) <= min(e.end, r[1])])

    wednesdays = (first_day + datetime.timedelta(days=7 * week) for week in range(50))
    ranges = ((d, d + datetime.timedelta(days=6)) for d in wednesdays)
    result = [(r[0] + datetime.timedelta(days=3), events_for_week(r)) for r in ranges]

    return warnings + '\n'.join('{}: {}'.format(day.strftime('%d %b %Y'), event_str) for (day, event_str) in result)

@app.route('/')
def index():
    if 'credentials' not in flask.session:
        return flask.redirect(flask.url_for('oauth2callback'))
    credentials = client.OAuth2Credentials.from_json(flask.session['credentials'])
    if credentials.access_token_expired:
        return flask.redirect(flask.url_for('oauth2callback'))
    else:
        http_auth = credentials.authorize(httplib2.Http())
    calendar_id = flask.request.args.get('calendarId') or 'primary'
    return '<pre>' + get_calendar_events(http_auth, calendar_id) + '</pre>'


@app.route('/oauth2callback')
def oauth2callback():
    flow = client.flow_from_clientsecrets(
        'client_secrets.json',
        scope='https://www.googleapis.com/auth/calendar.readonly',
        redirect_uri=flask.url_for('oauth2callback', _external=True))
    if 'code' not in flask.request.args:
        auth_uri = flow.step1_get_authorize_url()
        return flask.redirect(auth_uri)
    else:
        auth_code = flask.request.args.get('code')
        credentials = flow.step2_exchange(auth_code)
        flask.session['credentials'] = credentials.to_json()
        return flask.redirect(flask.url_for('index'))
