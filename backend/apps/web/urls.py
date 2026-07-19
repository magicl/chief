# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.web import config_views, resource_events, views
from django.urls import path

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('events/', resource_events.resource_events_sse, name='resource_events_sse'),
    path('partials/agents/', views.dashboard_agents_partial, name='dashboard_agents_partial'),
    path('partials/keys/', views.settings_keys_partial, name='settings_keys_partial'),
    path('agents/create/', config_views.agent_create, name='agent_create'),
    path('agents/create/mutate/', config_views.agent_create_mutate, name='agent_create_mutate'),
    path('agents/<uuid:agent_id>/', views.agent_detail, name='agent_detail'),
    path('agents/<uuid:agent_id>/config/', config_views.agent_config, name='agent_config'),
    path('agents/<uuid:agent_id>/config/save/', config_views.agent_config_save, name='agent_config_save'),
    path('agents/<uuid:agent_id>/config/mutate/', config_views.agent_config_mutate, name='agent_config_mutate'),
    path(
        'agents/<uuid:agent_id>/config/history/<uuid:config_id>/',
        config_views.agent_config_history,
        name='agent_config_history',
    ),
    path('agents/config/catalog/', config_views.agent_config_catalog, name='agent_config_catalog'),
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
    path('settings/keys/named/<str:name>/delete/', views.settings_keys_delete_named, name='settings_keys_delete_named'),
    path(
        'settings/keys/oauth/<uuid:credential_id>/authorize/',
        views.settings_keys_oauth_authorize,
        name='settings_keys_oauth_authorize',
    ),
    path(
        'settings/keys/oauth/<uuid:credential_id>/disconnect/',
        views.settings_keys_oauth_disconnect,
        name='settings_keys_oauth_disconnect',
    ),
    path(
        'settings/keys/oauth/google/callback/',
        views.settings_keys_oauth_google_callback,
        name='settings_keys_oauth_google_callback',
    ),
    path(
        'settings/keys/oauth/dropbox/callback/',
        views.settings_keys_oauth_dropbox_callback,
        name='settings_keys_oauth_dropbox_callback',
    ),
]
