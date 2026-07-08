package com.autocar.app.data.model

import kotlinx.serialization.Serializable

@Serializable
data class PathSummary(
    val id: Int,
    val name: String = "Untitled",
    val path_type: String = "sequential",
    val steps: Int = 0,
)

@Serializable
data class CreatePath(
    val name: String = "Untitled",
    val path_type: String = "sequential",
)

@Serializable
data class RenamePath(
    val name: String,
)

@Serializable
data class PathDetail(
    val id: Int,
    val name: String = "Untitled",
    val path_type: String = "sequential",
    val steps: List<PathStep> = emptyList(),
)

@Serializable
data class PathStep(
    val r: Float = 0f,
    val theta: Float = 0f,
    val omega: Float = 0f,
    val vel_pct: Float = 30f,
)

@Serializable
data class PathCreateResult(
    val ok: Boolean = false,
    val id: Int = 0,
)

@Serializable
data class PathStatus(
    val status: String = "idle",
    val path_id: Int? = null,
    val step: Int? = null,
    val total_steps: Int? = null,
)

@Serializable
data class RecordStart(
    val name: String = "Recording",
    val sample_rate: Int = 20,
)

@Serializable
data class RecordSample(
    val vx: Float = 0f,
    val vy: Float = 0f,
    val trans_speed: Float = 0f,
    val rot_speed: Float = 0f,
)

@Serializable
data class RecordStop(
    val num_coefficients: Int = 50,
)
