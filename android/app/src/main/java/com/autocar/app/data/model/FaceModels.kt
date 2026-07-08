package com.autocar.app.data.model

import kotlinx.serialization.Serializable

@Serializable
data class FaceState(
    val status: String = "idle",
    val tracking: Boolean = false,
    val rot_speed: Float = 0f,
    val vx: Float = 0f,
    val target_area: Float? = null,
    val selected_area: Float? = null,
    val faces: Int = 0,
    val fps: Float = 0f,
)

@Serializable
data class ClickBody(
    val x: Float,
    val y: Float,
)

@Serializable
data class StartBody(
    val backend: String = "mediapipe",
    val camera: Int = 0,
    val follow: Boolean = false,
)
