package com.autocar.app.data.api

import com.autocar.app.data.model.ApiResult
import com.autocar.app.data.model.ClickBody
import com.autocar.app.data.model.FaceState
import com.autocar.app.data.model.StartBody
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

interface FaceApi {
    @POST("api/face/start")
    suspend fun start(@Body body: StartBody = StartBody()): ApiResult

    @POST("api/face/click")
    suspend fun click(@Body body: ClickBody): ApiResult

    @POST("api/face/confirm")
    suspend fun confirm(): ApiResult

    @POST("api/face/reset")
    suspend fun reset(): ApiResult

    @POST("api/face/stop")
    suspend fun stop(): ApiResult

    @GET("api/face/status")
    suspend fun status(): FaceState
}
