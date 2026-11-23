-- BotConfig keys for AR/EN welcome + force-sub (idempotent inserts)
INSERT INTO bot_config (id, key, value, updated_at)
SELECT gen_random_uuid(), 'welcome_message_en',
  '"Welcome! Browse the store or tap Open Store."'::jsonb, NOW()
WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'welcome_message_en');

INSERT INTO bot_config (id, key, value, updated_at)
SELECT gen_random_uuid(), 'force_subscribe_enabled', 'false'::jsonb, NOW()
WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'force_subscribe_enabled');
