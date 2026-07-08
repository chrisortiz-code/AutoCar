package com.autocar.app.data.api

import com.autocar.app.data.model.ApiResult
import com.autocar.app.data.model.CreatePath
import com.autocar.app.data.model.PathCreateResult
import com.autocar.app.data.model.PathDetail
import com.autocar.app.data.model.PathStatus
import com.autocar.app.data.model.PathSummary
import com.autocar.app.data.model.RecordStart
import com.autocar.app.data.model.RecordStop
import com.autocar.app.data.model.RenamePath
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path

interface PathsApi {
    @GET("api/paths")
    suspend fun list(): List<PathSummary>

    @POST("api/paths")
    suspend fun create(@Body body: CreatePath): PathCreateResult

    @GET("api/paths/{id}")
    suspend fun get(@Path("id") id: Int): PathDetail

    @PATCH("api/paths/{id}")
    suspend fun rename(@Path("id") id: Int, @Body body: RenamePath): ApiResult

    @DELETE("api/paths/{id}")
    suspend fun delete(@Path("id") id: Int): ApiResult

    @POST("api/paths/{id}/execute")
    suspend fun execute(@Path("id") id: Int): ApiResult

    @POST("api/paths/{id}/stop")
    suspend fun stopExecution(@Path("id") id: Int): ApiResult

    @GET("api/status")
    suspend fun status(): PathStatus

    @POST("api/record/start")
    suspend fun recordStart(@Body body: RecordStart): PathCreateResult

    @POST("api/record/stop")
    suspend fun recordStop(@Body body: RecordStop = RecordStop()): ApiResult
}
