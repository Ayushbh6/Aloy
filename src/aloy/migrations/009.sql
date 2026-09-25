ALTER TABLE reservations ADD COLUMN provider TEXT NOT NULL DEFAULT 'unattributed';
ALTER TABLE spend_events ADD COLUMN provider TEXT NOT NULL DEFAULT 'unattributed';
CREATE INDEX reservations_provider_month ON reservations(provider,created_at);
CREATE INDEX spend_events_provider_month ON spend_events(provider,created_at);
