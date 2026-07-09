package com.autocar.app.data.api

import com.autocar.app.data.model.ApiResult
import com.autocar.app.data.model.SetRefBody
import com.autocar.app.data.model.TrackStartBody
import com.autocar.app.data.model.TrackState
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

interface TrackApi {
    @POST("api/track/start")
    suspend fun start(@Body body: TrackStartBody = TrackStartBody()): ApiResult

    @POST("api/track/set_ref")
    suspend fun setRef(@Body body: SetRefBody): ApiResult

    @POST("api/track/reset")
    suspend fun reset(): ApiResult

    @POST("api/track/stop")
    suspend fun stop(): ApiResult

    @GET("api/track/status")
    suspend fun status(): TrackState
}
