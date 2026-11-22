package com.autocar.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class RlState(
    val status: String = "idle",
    @SerialName("status_msg") val statusMsg: String = "",
    val confidence: Float = 0f,
    val actions: List<Float> = listOf(0f, 0f, 0f),
    @SerialName("lstm_step") val lstmStep: Int = 0,
    val fps: Float = 0f,
    @SerialName("desired_dist") val desiredDist: Float = 1f,
    @SerialName("target_depth") val targetDepth: Int = 0,
    @SerialName("cur_depth") val curDepth: Int = 0,
    @SerialName("csrt_ms") val csrtMs: Float = 0f,
    @SerialName("preprocess_ms") val preprocessMs: Float = 0f,
    @SerialName("policy_ms") val policyMs: Float = 0f,
    @SerialName("total_ms") val totalMs: Float = 0f,
    @SerialName("motor_cmd") val motorCmd: List<Float> = listOf(0f, 0f, 0f, 0f),
    @SerialName("model_variant") val modelVariant: String = "",
    @SerialName("model_name") val modelName: String = "",
    @SerialName("action_dim") val actionDim: Int = 3,
    @SerialName("safety_active") val safetyActive: Boolean = false,
)

@Serializable
data class RlCheckpointList(
    val checkpoints: List<String> = emptyList(),
)

@Serializable
data class RlStartBody(
    @SerialName("desired_dist") val desiredDist: Float = 1.0f,
    val checkpoint: String = "",
    val backend: String = "csrt",
)

@Serializable
data class SafetyStatus(
    val enabled: Boolean = false,
    @SerialName("lidar_connected") val lidarConnected: Boolean = false,
    @SerialName("last_action") val lastAction: String = "unknown",
    @SerialName("nearest_mm") val nearestMm: Int = -1,
    @SerialName("receiver_online") val receiverOnline: Boolean = false,
)

@Serializable
data class LidarScanResult(
    val points: List<LidarScanPoint> = emptyList(),
    @SerialName("min_mm") val minMm: Int? = null,
    @SerialName("max_mm") val maxMm: Int? = null,
)

@Serializable
data class LidarScanPoint(
    val angle: Float = 0f,
    @SerialName("dist_mm") val distMm: Float = 0f,
    val quality: Int = 0,
)

@Serializable
data class RlSetRefBody(
    val x1: Float,
    val y1: Float,
    val x2: Float,
    val y2: Float,
)
