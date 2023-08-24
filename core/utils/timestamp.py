import datetime


def get_current_timestamp():
    ct = datetime.datetime.now()
    ts = int(ct.timestamp())
    return ts
