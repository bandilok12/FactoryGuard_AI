-- FactoryGuard AI — database schema (mysql)
-- Generated from the SQLAlchemy models in backend/app/models.py
-- 17 tables: users, shifts, shift_assignments, machines, production_logs, sensor_readings,
-- maintenance_tickets, ticket_activities, spare_parts, part_usages, quality_inspections,
-- downtime_events, alerts, failure_predictions, settings, audit_logs, genai_logs
-- Load sample data by starting the app once (auto-seed) or: cd backend && python -m app.seed --reset


CREATE TABLE machines (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	code VARCHAR(20) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	machine_type VARCHAR(50) NOT NULL, 
	manufacturer VARCHAR(80), 
	model VARCHAR(80), 
	serial_no VARCHAR(80), 
	line VARCHAR(40), 
	install_date DATE, 
	rated_speed FLOAT NOT NULL, 
	power_kw FLOAT, 
	criticality VARCHAR(1) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	pm_interval_days INTEGER NOT NULL, 
	last_pm_date DATE, 
	temp_max FLOAT NOT NULL, 
	vibration_max FLOAT NOT NULL, 
	pressure_min FLOAT NOT NULL, 
	pressure_max FLOAT NOT NULL, 
	specs TEXT, 
	health_score FLOAT NOT NULL, 
	failure_risk FLOAT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_machines_machine_type ON machines (machine_type);
CREATE UNIQUE INDEX ix_machines_code ON machines (code);

CREATE TABLE settings (
	`key` VARCHAR(50) NOT NULL, 
	value VARCHAR(255) NOT NULL, 
	description VARCHAR(255), 
	PRIMARY KEY (`key`)
);


CREATE TABLE spare_parts (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	part_no VARCHAR(30) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	category VARCHAR(40) NOT NULL, 
	compatible_types VARCHAR(255), 
	quantity INTEGER NOT NULL, 
	reorder_level INTEGER NOT NULL, 
	reorder_qty INTEGER NOT NULL, 
	unit_cost FLOAT NOT NULL, 
	location VARCHAR(40), 
	supplier VARCHAR(80), 
	lead_time_days INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_part_qty CHECK (quantity >= 0)
);

CREATE UNIQUE INDEX ix_spare_parts_part_no ON spare_parts (part_no);

CREATE TABLE users (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	username VARCHAR(50) NOT NULL, 
	full_name VARCHAR(120) NOT NULL, 
	email VARCHAR(120), 
	phone VARCHAR(20), 
	password_hash VARCHAR(255) NOT NULL, 
	`role` VARCHAR(20) NOT NULL, 
	is_active BOOL NOT NULL, 
	failed_attempts INTEGER NOT NULL, 
	locked_until DATETIME, 
	last_login DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_user_role CHECK (role in ('admin','plant_head','technician','operator')), 
	UNIQUE (email)
);

CREATE UNIQUE INDEX ix_users_username ON users (username);
CREATE INDEX ix_users_role ON users (`role`);

CREATE TABLE alerts (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	kind VARCHAR(20) NOT NULL, 
	severity VARCHAR(10) NOT NULL, 
	machine_id INTEGER, 
	part_id INTEGER, 
	message VARCHAR(500) NOT NULL, 
	created_at DATETIME NOT NULL, 
	acknowledged BOOL NOT NULL, 
	acknowledged_by_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(machine_id) REFERENCES machines (id) ON DELETE CASCADE, 
	FOREIGN KEY(part_id) REFERENCES spare_parts (id) ON DELETE CASCADE, 
	FOREIGN KEY(acknowledged_by_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_alerts_created_at ON alerts (created_at);
CREATE INDEX ix_alerts_kind ON alerts (kind);
CREATE INDEX ix_alerts_acknowledged ON alerts (acknowledged);

CREATE TABLE audit_logs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER, 
	action VARCHAR(40) NOT NULL, 
	entity VARCHAR(40), 
	entity_id VARCHAR(40), 
	detail TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);

CREATE TABLE failure_predictions (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	machine_id INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	probability FLOAT NOT NULL, 
	risk_level VARCHAR(10) NOT NULL, 
	predicted_mode VARCHAR(40), 
	top_factors TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(machine_id) REFERENCES machines (id) ON DELETE CASCADE
);

CREATE INDEX ix_failure_predictions_created_at ON failure_predictions (created_at);
CREATE INDEX ix_failure_predictions_machine_id ON failure_predictions (machine_id);

CREATE TABLE genai_logs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER, 
	feature VARCHAR(40) NOT NULL, 
	prompt TEXT NOT NULL, 
	response TEXT NOT NULL, 
	provider VARCHAR(20) NOT NULL, 
	latency_ms INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);


CREATE TABLE maintenance_tickets (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	ticket_no VARCHAR(20) NOT NULL, 
	machine_id INTEGER NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	description TEXT, 
	category VARCHAR(20) NOT NULL, 
	priority VARCHAR(10) NOT NULL, 
	status VARCHAR(15) NOT NULL, 
	ticket_type VARCHAR(15) NOT NULL, 
	source VARCHAR(20) NOT NULL, 
	reported_by_id INTEGER, 
	assigned_to_id INTEGER, 
	created_at DATETIME NOT NULL, 
	assigned_at DATETIME, 
	started_at DATETIME, 
	resolved_at DATETIME, 
	closed_at DATETIME, 
	root_cause TEXT, 
	action_taken TEXT, 
	labour_hours FLOAT, 
	ai_triage TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(machine_id) REFERENCES machines (id) ON DELETE CASCADE, 
	FOREIGN KEY(reported_by_id) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(assigned_to_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_maintenance_tickets_created_at ON maintenance_tickets (created_at);
CREATE INDEX ix_maintenance_tickets_machine_id ON maintenance_tickets (machine_id);
CREATE INDEX ix_maintenance_tickets_status ON maintenance_tickets (status);
CREATE UNIQUE INDEX ix_maintenance_tickets_ticket_no ON maintenance_tickets (ticket_no);
CREATE INDEX ix_maintenance_tickets_priority ON maintenance_tickets (priority);

CREATE TABLE shifts (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	code VARCHAR(5) NOT NULL, 
	name VARCHAR(50) NOT NULL, 
	start_time VARCHAR(5) NOT NULL, 
	end_time VARCHAR(5) NOT NULL, 
	supervisor_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (code), 
	FOREIGN KEY(supervisor_id) REFERENCES users (id) ON DELETE SET NULL
);


CREATE TABLE downtime_events (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	machine_id INTEGER NOT NULL, 
	shift_id INTEGER, 
	ticket_id INTEGER, 
	reported_by_id INTEGER, 
	start_time DATETIME NOT NULL, 
	end_time DATETIME, 
	duration_min FLOAT, 
	reason_category VARCHAR(30) NOT NULL, 
	reason_detail TEXT, 
	is_planned BOOL NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(machine_id) REFERENCES machines (id) ON DELETE CASCADE, 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(ticket_id) REFERENCES maintenance_tickets (id) ON DELETE SET NULL, 
	FOREIGN KEY(reported_by_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_downtime_events_reason_category ON downtime_events (reason_category);
CREATE INDEX ix_downtime_events_machine_id ON downtime_events (machine_id);
CREATE INDEX ix_downtime_events_start_time ON downtime_events (start_time);

CREATE TABLE part_usages (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	ticket_id INTEGER NOT NULL, 
	part_id INTEGER NOT NULL, 
	quantity INTEGER NOT NULL, 
	used_by_id INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(ticket_id) REFERENCES maintenance_tickets (id) ON DELETE CASCADE, 
	FOREIGN KEY(part_id) REFERENCES spare_parts (id), 
	FOREIGN KEY(used_by_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_part_usages_ticket_id ON part_usages (ticket_id);

CREATE TABLE production_logs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	machine_id INTEGER NOT NULL, 
	shift_id INTEGER NOT NULL, 
	operator_id INTEGER, 
	log_date DATE NOT NULL, 
	product_name VARCHAR(120) NOT NULL, 
	batch_no VARCHAR(40) NOT NULL, 
	planned_qty INTEGER NOT NULL, 
	produced_qty INTEGER NOT NULL, 
	rejected_qty INTEGER NOT NULL, 
	planned_time_min FLOAT NOT NULL, 
	run_time_min FLOAT NOT NULL, 
	ideal_cycle_sec FLOAT NOT NULL, 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_prod_qty CHECK (produced_qty >= 0 AND rejected_qty >= 0 AND rejected_qty <= produced_qty), 
	FOREIGN KEY(machine_id) REFERENCES machines (id) ON DELETE CASCADE, 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(operator_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_production_logs_batch_no ON production_logs (batch_no);
CREATE INDEX ix_production_logs_machine_id ON production_logs (machine_id);
CREATE INDEX ix_production_logs_log_date ON production_logs (log_date);
CREATE INDEX ix_prod_machine_date ON production_logs (machine_id, log_date);

CREATE TABLE sensor_readings (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	machine_id INTEGER NOT NULL, 
	shift_id INTEGER, 
	operator_id INTEGER, 
	recorded_at DATETIME NOT NULL, 
	temperature FLOAT NOT NULL, 
	vibration FLOAT NOT NULL, 
	pressure FLOAT NOT NULL, 
	rpm FLOAT NOT NULL, 
	torque FLOAT NOT NULL, 
	tool_wear FLOAT NOT NULL, 
	power_kw FLOAT, 
	humidity FLOAT, 
	source VARCHAR(12) NOT NULL, 
	is_anomaly BOOL NOT NULL, 
	anomaly_score FLOAT, 
	anomaly_reason VARCHAR(255), 
	PRIMARY KEY (id), 
	FOREIGN KEY(machine_id) REFERENCES machines (id) ON DELETE CASCADE, 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(operator_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_sensor_readings_recorded_at ON sensor_readings (recorded_at);
CREATE INDEX ix_sensor_readings_is_anomaly ON sensor_readings (is_anomaly);
CREATE INDEX ix_sensor_machine_time ON sensor_readings (machine_id, recorded_at);
CREATE INDEX ix_sensor_readings_machine_id ON sensor_readings (machine_id);

CREATE TABLE shift_assignments (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	shift_id INTEGER NOT NULL, 
	machine_id INTEGER, 
	work_date DATE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_user_day UNIQUE (user_id, work_date), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(shift_id) REFERENCES shifts (id) ON DELETE CASCADE, 
	FOREIGN KEY(machine_id) REFERENCES machines (id) ON DELETE SET NULL
);

CREATE INDEX ix_shift_assignments_work_date ON shift_assignments (work_date);
CREATE INDEX ix_shift_assignments_user_id ON shift_assignments (user_id);

CREATE TABLE ticket_activities (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	ticket_id INTEGER NOT NULL, 
	user_id INTEGER, 
	kind VARCHAR(20) NOT NULL, 
	message TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(ticket_id) REFERENCES maintenance_tickets (id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_ticket_activities_ticket_id ON ticket_activities (ticket_id);

CREATE TABLE quality_inspections (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	batch_no VARCHAR(40) NOT NULL, 
	machine_id INTEGER NOT NULL, 
	production_log_id INTEGER, 
	shift_id INTEGER, 
	inspector_id INTEGER, 
	inspected_at DATETIME NOT NULL, 
	sample_size INTEGER NOT NULL, 
	defects_found INTEGER NOT NULL, 
	defect_type VARCHAR(40), 
	seal_temp FLOAT, 
	seal_pressure FLOAT, 
	line_speed FLOAT, 
	fill_weight_dev FLOAT, 
	humidity FLOAT, 
	result VARCHAR(10) NOT NULL, 
	ai_defect_risk FLOAT, 
	notes TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_qc_defects CHECK (defects_found >= 0 AND defects_found <= sample_size), 
	FOREIGN KEY(machine_id) REFERENCES machines (id) ON DELETE CASCADE, 
	FOREIGN KEY(production_log_id) REFERENCES production_logs (id) ON DELETE SET NULL, 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(inspector_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_quality_inspections_inspected_at ON quality_inspections (inspected_at);
CREATE INDEX ix_quality_inspections_batch_no ON quality_inspections (batch_no);
CREATE INDEX ix_quality_inspections_machine_id ON quality_inspections (machine_id);
