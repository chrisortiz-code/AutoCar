package com.autocar.app.data.model

import kotlinx.serialization.Serializable

@Serializable
data class ColorState(
    val status: String = "idle",
    val status_msg: String = "",
    val picked_color: List<Int>? = null,
    val confidence: Float = 0f,
    val rot_speed: Float = 0f,
    val vx: Float = 0f,
    val target_depth: Int? = null,
    val current_depth: Int? = null,
    val fps: Float = 0f,
    val sensitivity: Int = 50,
)

@Serializable
data class ColorStartBody(
    val follow: Boolean = true,
)

@Serializable
data class ColorClickBody(
    val x: Float,
    val y: Float,
)

@Serializable
data class ColorSensitivityBody(
    val value: Int,
)
