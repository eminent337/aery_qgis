"""Tests for TranscriptView session loading and message restoration."""

from PyQt6.QtWidgets import QLabel
from aery_plugin.transcript_view import TranscriptView, MessageBubble


def test_transcript_set_session_messages_restores_bubbles():
    tv = TranscriptView()
    test_messages = [
        {"role": "user", "text": "Hello Aery", "time": "12:00:00"},
        {"role": "assistant", "text": "Hello! How can I help you?", "time": "12:00:01"},
    ]
    tv.set_session_messages(test_messages)
    assert tv.get_session_messages() == test_messages
    
    # Check that MessageBubble widgets were created in the layout
    bubbles = []
    for i in range(tv._feed_layout.count()):
        w = tv._feed_layout.itemAt(i).widget()
        if isinstance(w, MessageBubble):
            bubbles.append(w)
            
    assert len(bubbles) == 2
    assert "Hello Aery" in bubbles[0]._body.text()
    assert "Hello! How can I help you?" in bubbles[1]._body.text()
