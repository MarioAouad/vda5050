# Autonomous Fleet Expansion Protocol
**Date:** July 2026
**Version:** 1.0

## 1. Overview
This document outlines the standard protocol for adding a new Autonomous Mobile Robot (AMR) to an existing VDA-5050 fleet. 

## 2. IP Assignment
When a new AMR is brought online, it must be assigned a static IP address in the `192.168.10.x` subnet. The AMR's network interface must be configured to prioritize 5GHz Wi-Fi bands to minimize latency during MQTT message bursts.

## 3. MQTT Broker Registration
The AMR must authenticate with the fleet's central MQTT broker using TLS 1.3. The initial `connection` message must contain the AMR's unique `serialNumber` in the header, otherwise the Master Control will reject the connection with an `INVALID_AUTH` error.

## 4. Safety Zone Calibration
Before accepting any orders, the AMR must navigate to the designated calibration zone and report an `IDLE` state. Once confirmed, the system will push the latest digital map to the AMR.
