package com.autocar.app.data.api

import com.autocar.app.data.model.ApiResult
import com.autocar.app.data.model.RlSetRefBody
import com.autocar.app.data.model.RlStartBody
import com.autocar.app.data.model.RlState
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

interface RlApi {
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
}
