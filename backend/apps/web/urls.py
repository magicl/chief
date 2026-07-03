# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.web import views
from django.urls import path

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('agents/bootstrap/', views.bootstrap_agent, name='bootstrap_agent'),
    path('agents/<uuid:agent_id>/', views.agent_detail, name='agent_detail'),
    path('agents/<uuid:agent_id>/chat/', views.agent_start_chat, name='agent_start_chat'),
    path('agents/<uuid:agent_id>/delete/', views.delete_agent, name='delete_agent'),
    path('agents/<uuid:agent_id>/start/', views.start_agent_session, name='start_agent_session'),
    path('debug/sse-spike/', views.sse_spike, name='sse_spike'),
    path('sessions/<uuid:session_id>/', views.session_detail, name='session_detail'),
    path('sessions/<uuid:session_id>/events/', views.session_events_sse, name='session_events_sse'),
    path('sessions/<uuid:session_id>/chat/', views.session_chat, name='session_chat'),
    path('sessions/<uuid:session_id>/pause/', views.session_pause, name='session_pause'),
    path('sessions/<uuid:session_id>/resume/', views.session_resume, name='session_resume'),
    path('sessions/<uuid:session_id>/abort/', views.session_abort, name='session_abort'),
    path('settings/keys/', views.settings_keys, name='settings_keys'),
    path('settings/keys/named/', views.settings_keys_add_named, name='settings_keys_add_named'),
    path('settings/keys/named/<str:name>/', views.settings_keys_update_named, name='settings_keys_update_named'),
    path('settings/keys/named/<str:name>/delete/', views.settings_keys_delete_named, name='settings_keys_delete_named'),
]
