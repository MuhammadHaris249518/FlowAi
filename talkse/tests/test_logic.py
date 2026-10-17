"""Unit tests for the call logic that has no network or database dependency.

Run from the repo root:  .venv/Scripts/python.exe -m pytest talkse/tests -q
"""

import booking_engine as be
import call_engine as ce
import clinic_config as config


# ---- Caller name / phone parsing ---------------------------------------------------

def test_bare_name_is_taken_when_the_llm_misses_it():
    assert ce._as_name("Riley Phonefix.") == "Riley Phonefix"
    assert ce._as_name("my name is tina timeout") == "Tina Timeout"
    assert ce._as_name("It's Mary-Jane O'Neil") == "Mary-Jane O'Neil"


def test_non_names_are_rejected():
    assert ce._as_name("Thursday at 2 PM works") is None
    assert ce._as_name("I want to book an AC repair today please") is None
    assert ce._as_name("123 Main St") is None


def test_phone_from_digits_and_spoken_words():
    assert ce._as_phone("512-555-0199") == "+15125550199"
    assert ce._as_phone("my number is 1 512 555 0199") == "+15125550199"
    assert ce._as_phone("five one two five five five zero one nine nine") == "+15125550199"
    assert ce._as_phone("no thanks") is None


def test_declining_the_phone_number():
    assert ce._DECLINE.search("no thanks")
    assert ce._DECLINE.search("I'd rather not")
    state = {"intent": "book", "service": "AC Repair", "area": "Austin", "preferred_time": "Thursday 10 AM",
             "caller_name": "Sam", "caller_phone": None, "phone_declined": True}
    assert ce.next_missing_field(state) is None


def test_phone_is_asked_after_the_name():
    state = {"intent": "book", "service": "AC Repair", "area": "Austin", "preferred_time": "Thursday 10 AM",
             "caller_name": "Sam", "caller_phone": None}
    assert ce.next_missing_field(state) == "caller_phone"


# ---- Booking read-back ---------------------------------------------------------------

def test_yes_and_changes_are_told_apart():
    assert ce._YES.search("yes please") and not ce._NO.search("yes please")
    assert ce._NO.search("yes but change it to 3 PM")
    assert ce._NO.search("no, that's wrong")


def test_confirmation_reads_back_the_booking():
    state = {"service": "AC Repair", "area": "Round Rock", "preferred_time": "Thursday at 10 AM",
             "caller_name": "Sam Lee", "caller_phone": "+15125550199"}
    text = ce._confirmation_prompt(state)
    assert "Shall I book it?" in text and "Round Rock" in text and "Sam Lee" in text and "Thursday" in text
    assert state["awaiting"] == "confirm"


def test_unclear_time_is_asked_again_not_guessed():
    state = {"service": "AC Repair", "area": "Austin", "preferred_time": "sometime soon", "caller_name": "Sam"}
    text = ce._confirmation_prompt(state)
    assert "didn't catch the day and time" in text
    assert state["preferred_time"] is None and state["awaiting"] == "preferred_time"


def test_changing_only_the_time_keeps_the_day():
    assert ce._merge_time_change("Friday at 9 AM", "11 AM") == "Friday at 11 AM"
    assert ce._merge_time_change("Friday at 9 AM", "Saturday") == "Saturday at 9 AM"
    assert ce._merge_time_change("Friday at 9 AM", "Monday at 3 PM") == "Monday at 3 PM"
    assert ce._merge_time_change(None, "Thursday 10 AM") == "Thursday 10 AM"


# ---- Time parsing ----------------------------------------------------------------------

def test_unparseable_time_returns_none():
    assert be.parse_time_string("sometime soon") is None
    assert be.parse_time_string("whenever works") is None
    assert be.parse_time_string("") is None


def test_weekday_time_is_in_the_future():
    dt = be.parse_time_string("Thursday at 10 AM")
    assert dt is not None and dt.strftime("%A") == "Thursday" and dt.hour == 10
    assert dt > config.now()


def test_tomorrow():
    dt = be.parse_time_string("tomorrow at 2pm")
    assert dt is not None and dt.hour == 14
    assert (dt.date() - config.now().date()).days == 1


# ---- Safety and approval keywords --------------------------------------------------------

def test_smoke_detector_beeping_is_not_an_emergency():
    assert be.check_emergency_protocol("my smoke detector keeps beeping") is None
    assert be.check_emergency_protocol("can you replace the CO detector battery") is None


def test_real_hazards_still_trigger():
    assert be.check_emergency_protocol("I smell gas in the kitchen")
    assert be.check_emergency_protocol("there is smoke coming from the outlet")
    assert be.check_emergency_protocol("the smoke alarm is going off and something is burning")


def test_keywords_match_whole_words_only():
    assert be.check_emergency_protocol("we ordered from the smokehouse") is None


def test_home_office_and_store_are_not_commercial_jobs():
    repair = config.get_service("AC Repair")
    assert be.check_owner_approval(repair, "I work from my home office and the AC broke", "Austin") is None
    assert be.check_owner_approval(repair, "I bought a new filter at the store", "Austin") is None


def test_commercial_jobs_still_need_the_owner():
    repair = config.get_service("AC Repair")
    assert be.check_owner_approval(repair, "it's for our restaurant kitchen", "Austin")
    assert be.check_owner_approval(config.get_service("AC Installation"), "new system please", "Austin")
    assert be.check_owner_approval(repair, "AC is broken", "San Antonio")
