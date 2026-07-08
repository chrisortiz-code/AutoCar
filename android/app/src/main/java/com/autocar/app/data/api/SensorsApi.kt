package com.autocar.app.data.api

import com.autocar.app.data.model.LidarScan
import com.autocar.app.data.model.SensorStatus
import retrofit2.http.GET

interface SensorsApi {
    @GET("api/sensors/status")
    suspend fun status(): SensorStatus

    @GET("api/sensors/lidar/scan")
    suspend fun lidarScan(): LidarScan
}
