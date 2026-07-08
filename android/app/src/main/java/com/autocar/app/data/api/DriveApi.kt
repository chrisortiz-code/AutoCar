package com.autocar.app.data.api

import com.autocar.app.data.model.ApiResult
import com.autocar.app.data.model.DriveStatus
import com.autocar.app.data.model.PolarMove
import com.autocar.app.data.model.TranslateRotateMove
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

interface DriveApi {
    @POST("api/drive/polar")
    suspend fun polar(@Body move: PolarMove): ApiResult

    @POST("api/drive/translate-rotate")
    suspend fun translateRotate(@Body move: TranslateRotateMove): ApiResult

    @POST("api/drive/stop")
    suspend fun stop(): ApiResult

    @POST("api/drive/estop")
    suspend fun estop(): ApiResult

    @GET("api/drive/status")
    suspend fun status(): DriveStatus
}
