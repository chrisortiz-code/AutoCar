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
data class DriveStatus(
    val connected: List<Int> = emptyList(),
    val moving: Boolean = false,
    val position: Float = 0f,
    val motors: Map<String, String> = emptyMap(),
)

@Serializable
data class ApiResult(
    val ok: Boolean = false,
    val message: String? = null,
    val error: String? = null,
)
