package com.autocar.app.data.api

import com.autocar.app.data.model.ApiResult
import com.autocar.app.data.model.RlCheckpointList
import com.autocar.app.data.model.RlSetRefBody
import com.autocar.app.data.model.RlStartBody
import com.autocar.app.data.model.RlState
import com.autocar.app.data.model.SafetyStatus
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

interface RlApi {
    @GET("api/rl/checkpoints")
    suspend fun checkpoints(): RlCheckpointList

    @POST("api/rl/start")
    suspend fun start(@Body body: RlStartBody = RlStartBody()): ApiResult

    @POST("api/rl/set_ref")
    suspend fun setRef(@Body body: RlSetRefBody): ApiResult

    @POST("api/rl/reset")
    suspend fun reset(): ApiResult

    @POST("api/rl/stop")
    suspend fun stop(): ApiResult

    @GET("api/rl/status")
    suspend fun status(): RlState

    @POST("api/safety/enable")
    suspend fun safetyEnable(): ApiResult

    @POST("api/safety/disable")
    suspend fun safetyDisable(): ApiResult

    @GET("api/safety/status")
    suspend fun safetyStatus(): SafetyStatus
}
