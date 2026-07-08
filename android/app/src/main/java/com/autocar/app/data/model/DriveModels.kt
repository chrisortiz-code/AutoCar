package com.autocar.app.data.model

import kotlinx.serialization.Serializable

@Serializable
data class PolarMove(
    val r: Float = 0f,
    val theta: Float = 0f,
    val omega: Float = 0f,
    val vel_pct: Float = 30f,
)

@Serializable
data class TranslateRotateMove(
    val r: Float = 0f,
    val theta: Float = 0f,
    val omega: Float = 0f,
    val vel_pct: Float = 30f,
)

@Serializable
data class MotorDetail(
    val role: String = "",
    val armed: Boolean = false,
    val error: Int = 0,
    val current: Float = 0f,
    val position: Float = 0f,
    val axis_state: Int = 0,
)

@Serializable
data class DriveStatus(
    val connected: List<Int> = emptyList(),
    val armed: List<Int> = emptyList(),
    val moving: Boolean = false,
    val motors: Map<String, MotorDetail> = emptyMap(),
    val receiver_online: Boolean = false,
    val uptime: Float = 0f,
)

@Serializable
data class ApiResult(
    val ok: Boolean = false,
    val message: String? = null,
    val error: String? = null,
)
