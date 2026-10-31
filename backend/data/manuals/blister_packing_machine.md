# Blister Packing Machine BP-250 — Service & Troubleshooting Manual
Machine type: Blister Packer
Manufacturer: Packtech Systems (synthetic manual for demo use)

## 1. Overview
The BP-250 is a flat-plate thermoforming blister packer for PVC/PVDC/Alu-Alu packs. Stations: PVC unwind, forming heater, forming station, feeding (tablet feeder with brush box), vision inspection, sealing station (heated sealing plate + knurled roller), embossing / batch coding, perforation and cutting (punch & die), reject chute.
Rated speed: 45-60 strokes/min (up to 6,000 blisters/hour). Forming temperature 120-140 °C. Sealing temperature 145-155 °C for PVC/Alu, 180-200 °C for Alu-Alu. Sealing pressure 4.0-5.0 bar. Compressed air 6 bar ±0.5.

## 2. Alarm / Error Codes
- E01 FORMING HEATER UNDER-TEMPERATURE: heater cartridge failed, thermocouple loose, SSR failed. Check heater resistance (should be 38-42 Ω), tighten thermocouple, replace SSR.
- E02 SEALING TEMPERATURE HIGH: PID tuning drift, thermocouple shorted, cooling fan stopped. Verify reading with a calibrated probe; replace thermocouple (part TC-K-150).
- E03 LOW AIR PRESSURE: compressor issue, leak in pneumatic line, FRL filter choked, regulator drift. Pressure below 4.5 bar stops the machine.
- E05 MAIN MOTOR OVERLOAD: jam at punch station, worn indexing gearbox, bearing seizure. Reset overload relay only after clearing jam.
- E07 VISION REJECT RATE HIGH: camera lens dirty, lighting failure, empty pockets from feeder.
- E09 FOIL BREAK: misaligned unwind, brake tension too high, splice failure.

## 3. Troubleshooting — High Vibration / Noise
Symptoms: vibration above 7.1 mm/s RMS (ISO 10816 zone C), rattling at indexing station, uneven pitch.
Probable causes and actions:
1. Main drive bearing wear (6205-2RS / 6206-2RS). Measure vibration at bearing housing; if > 7.1 mm/s or rising trend, replace bearing. Use bearing puller; heat new bearing to 80 °C with induction heater; never hammer.
2. Loose mounting bolts on index gearbox — torque to 45 Nm in star pattern.
3. Worn timing belt (HTD-8M) — check tension (deflection 6-8 mm at 30 N), replace if cracked.
4. Misaligned coupling — check with dial gauge, max 0.05 mm offset.
5. Lack of lubrication — grease bearings with NLGI-2 lithium grease every 500 running hours.
After repair, run machine empty for 15 minutes and re-measure vibration.

## 4. Troubleshooting — Seal Leak / Poor Seal Quality
Symptoms: leak test (methylene blue / vacuum) failures, wrinkled lidding foil, channel leaks.
1. Sealing temperature outside 145-155 °C — check PID set point and thermocouple.
2. Sealing pressure outside 4.0-5.0 bar — adjust regulator, check cylinder seals.
3. Knurling pattern worn on sealing plate — inspect, re-machine or replace plate (part SP-BP250).
4. Dwell time too short because line speed too high (> 52 strokes/min) — reduce speed.
5. Contaminated foil or high humidity (> 55% RH) causing PVC moisture — check dehumidifier.
6. Parallelism of upper/lower sealing plates — check with carbon paper impression test.

## 5. Troubleshooting — Overheating
Symptoms: motor or heater-zone temperature rising above 75 °C, E02 alarms.
1. Cooling fan of panel or motor failed — check fan current, clean filters.
2. Chiller water flow to forming/sealing station low — check flow switch (min 4 L/min).
3. Heater SSR stuck ON — measure output; replace SSR (part SSR-40A).
4. Ambient temperature too high — keep room below 27 °C.

## 6. Troubleshooting — Low Pneumatic Pressure
1. Check main FRL unit — drain water trap, replace filter element (part FRL-FLT-05) if pressure drop > 0.5 bar across filter.
2. Listen for leaks; use soap solution at push-in fittings and cylinder rod seals.
3. Replace leaking solenoid valve (part SV-5/2-24V) or cylinder seal kit (part CYL-SK-32).
4. Confirm compressor discharge pressure is 7 bar.

## 7. Punch & Die / Tool Wear
Cutting burrs, incomplete perforation and torque increase indicate worn punch & die. Replace or regrind after 200 running hours (tool-wear counter). Torque on index motor > 70 Nm combined with high tool wear indicates overstrain — stop and change tooling (part PD-BP250).

## 8. Preventive Maintenance Schedule
- Daily: clean sealing plate, check air pressure, drain FRL, verify vision camera.
- Weekly: check belt tension, inspect cutting tool, lubricate guide rails.
- Monthly (every 30 days): grease bearings, check thermocouples, calibrate sealing temperature, leak-test pneumatic system.
- Quarterly: replace FRL filter, check gearbox oil (ISO VG 220), inspect electrical terminals with thermal camera.

## 9. Safety
Apply Lock-Out / Tag-Out (LOTO) before opening guards. Wait 10 minutes for heaters to cool below 50 °C. Bleed compressed air before working on pneumatic components. Use heat-resistant gloves.
