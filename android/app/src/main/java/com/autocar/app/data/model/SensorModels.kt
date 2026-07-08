package com.autocar.app.data.model

import kotlinx.serialization.Serializable

@Serializable
data class SensorStatus(
    val camera: CameraStatus = CameraStatus(),
    val lidar: LidarStatus = LidarStatus(),
)

@Serializable
data class CameraStatus(
    val fps: Float = 0f,
    val connected: Boolean = false,
    val resolution: List<Int> = emptyList(),
)

@Serializable
data class LidarStatus(
    val connected: Boolean = false,
    val scan_hz: Float = 0f,
    val count: Int = 0,
    val port: String? = null,
    val error: String? = null,
)

@Serializable
data class LidarScan(
    val points: List<List<Float>> = emptyList(),
    val connected: Boolean = false,
    val scan_hz: Float = 0f,
    val count: Int = 0,
)

@Serializable
data class LidarPoint(
    val angle: Float = 0f,
    val dist_mm: Float = 0f,
    val quality: Int = 0,
)

@Serializable
data class MotorsWsData(
    val connected: List<Int> = emptyList(),
    val armed: List<Int> = emptyList(),
    val motors: Map<String, MotorDetail> = emptyMap(),
    val receiver_online: Boolean = false,
    val uptime: Float = 0f,
)

@Serializable
data class SensorUpdate(
    val type: String = "",
    val lidar: LidarWsData? = null,
    val camera: CameraWsData? = null,
    val drive: DriveWsData? = null,
    val motors: MotorsWsData? = null,
)

@Serializable
data class LidarWsData(
    val points: List<LidarPoint> = emptyList(),
    val scan_hz: Float = 0f,
    val connected: Boolean = false,
)

@Serializable
data class CameraWsData(
    val fps: Float = 0f,
    val connected: Boolean = false,
    val resolution: List<Int> = emptyList(),
)

@Serializable
data class DriveWsData(
    val moving: Boolean = false,
    val mode: String = "idle",
)
