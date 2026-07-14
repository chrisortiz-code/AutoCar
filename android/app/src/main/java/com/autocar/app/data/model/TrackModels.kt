package com.autocar.app.data.model

import kotlinx.serialization.Serializable

@Serializable
data class TrackState(
    val status: String = "idle",
    val status_msg: String = "",
    val confidence: Float = 0f,
    val rot_speed: Float = 0f,
    val vx: Float = 0f,
    val fps: Float = 0f,
    val backend: String = "orb",
)

@Serializable
data class SetRefBody(
    val x1: Float,
    val y1: Float,
    val x2: Float,
    val y2: Float,
)

@Serializable
data class TrackStartBody(
    val backend: String = "orb",
    val follow: Boolean = true,
)
