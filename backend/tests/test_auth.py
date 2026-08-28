import time

from app.auth import create_access_token, decode_access_token, hash_password, verify_password

SECRET = "test-secret"
ALGORITHM = "HS256"


def test_hash_password_does_not_store_the_plaintext():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"


def test_verify_password_accepts_the_correct_password():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_a_wrong_password():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False


def test_access_token_round_trips_the_user_id():
    token = create_access_token(user_id=42, secret=SECRET, algorithm=ALGORITHM, expires_minutes=60)
    user_id = decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)
    assert user_id == 42


def test_decode_rejects_a_token_signed_with_a_different_secret():
    token = create_access_token(user_id=42, secret="a-different-secret", algorithm=ALGORITHM, expires_minutes=60)
    assert decode_access_token(token, secret=SECRET, algorithm=ALGORITHM) is None


def test_decode_rejects_an_expired_token():
    token = create_access_token(user_id=42, secret=SECRET, algorithm=ALGORITHM, expires_minutes=0)
    time.sleep(1.1)
    assert decode_access_token(token, secret=SECRET, algorithm=ALGORITHM) is None


def test_decode_rejects_garbage_input():
    assert decode_access_token("not-a-real-token", secret=SECRET, algorithm=ALGORITHM) is None
