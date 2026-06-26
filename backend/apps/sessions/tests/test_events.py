# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.sessions.events import append_event, events_for
from apps.sessions.models import AgentSessionEventKind
from apps.sessions.tests.base import make_test_session

from olib.py.django.test.cases import OTransactionTestCase


class TestAppendEvent(OTransactionTestCase):
    def test_seq_monotonic(self) -> None:
        session = make_test_session()
        e1 = append_event(session, AgentSessionEventKind.INPUT, {'content': 'hi'})
        e2 = append_event(session, AgentSessionEventKind.OUTPUT, {'content': 'hello'})
        self.assertEqual(e1.seq, 1)
        self.assertEqual(e2.seq, 2)
        ordered = events_for(session)
        self.assertEqual([e.seq for e in ordered], [1, 2])
