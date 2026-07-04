/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
interface AgentConfigUrls {
  csrf?: string;
  save?: string;
  mutate?: string;
  credentials?: Record<string, Array<{ name: string; is_set: boolean }>>;
}

interface Window {
  __AGENT_CONFIG_URLS__?: AgentConfigUrls;
}
