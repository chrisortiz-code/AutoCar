package com.autocar.app.data.api

import com.autocar.app.data.model.ApiResult
import com.autocar.app.data.model.ColorClickBody
import com.autocar.app.data.model.ColorStartBody
import com.autocar.app.data.model.ColorState
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

interface ColorApi {
    @POST("api/color/start")
    suspend fun start(@Body body: ColorStartBody = ColorStartBody()): ApiResult

    @POST("api/color/click")
    suspend fun click(@Body body: ColorClickBody): ApiResult

    @POST("api/color/confirm")
    suspend fun confirm(): ApiResult

    @POST("api/color/reset")
    suspend fun reset(): ApiResult

    @POST("api/color/stop")
    suspend fun stop(): ApiResult

    @GET("api/color/status")
    suspend fun status(): ColorState
}
