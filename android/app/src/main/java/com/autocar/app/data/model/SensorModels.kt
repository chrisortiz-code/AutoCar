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
data class SensorUpdate(
    val type: String = "",
    val lidar: LidarWsData? = null,
    val camera: CameraWsData? = null,
    val drive: DriveWsData? = null,
)

@Serializable
data class LidarWsData(
    val points: List<List<Float>> = emptyList(),
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
