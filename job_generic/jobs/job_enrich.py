
#import atexit
#import logging
import boto3
import io
import time
import os
import json
import random
from pyspark.sql import SparkSession, DataFrame, Row
from pyspark.sql.types import StructType, StructField, StringType, IntegerType, BooleanType ,ArrayType
from pyspark.sql.functions import *
from pyspark import SparkContext ,SparkConf
from functools import reduce 
from collections import OrderedDict
from datetime import datetime, timedelta
from urllib import request, parse


def enricher(union,spark):
    
    """
    #Función para escribir en s3 logs
    def write_logs(body, bucket, key):
        session = boto3.Session(profile_name="crossingover")
        s3 = session.client("s3")
        s3.put_object(Body=body, Bucket=bucket, Key=key)

    #armo el logger
    log = logging.getLogger("Enrich")
    log_stringio = io.StringIO()
    handler = logging.StreamHandler(log_stringio)
    log.addHandler(handler)
    #defino el nivel de logging
    logging.basicConfig(level=logging.INFO)
    """

    # bloque fecha
    day = datetime.now()
    day = day.strftime("%Y-%m-%d %H:%M:%S.%f")

    #bloque tiempo
    start = time.time()

    # Creacion del diccionario para el request

    hes = union.head().readingsSource
    owner = union.head().owner
    guid = union.head().guidFile
    
    dicc = {"owner": {
                            "hes": hes,
                            "owner": owner,
                            "guidFile": guid
                    }
                    }


    # creo el dataframe con valores unicos tanto de servicePointId como de deviceId

    df_relationDevice = union.dropDuplicates((["servicePointId","deviceId"]))
    df_servicePoint = df_relationDevice.dropDuplicates((["servicePointId"]))
    df_device = df_relationDevice.dropDuplicates((["deviceId"]))

    df_servicePointVariable_prof = union.filter(union.readingType == "LOAD PROFILE READING")
    df_servicePointVariable_prof = df_servicePointVariable_prof.dropDuplicates((["readingType","variableId","servicePointId"
                                    ,"meteringType","unitOfMeasure","logNumber","channel","intervalSize"]))

    df_servicePointVariable_regEvent = union.filter((union.readingType == "REGISTERS") | (union.readingType == "EVENTS"))
    df_servicePointVariable_regEvent = df_servicePointVariable_regEvent.dropDuplicates((["readingType","variableId","servicePointId",
                                    "meteringType","unitOfMeasure"]))


    # genero el diccionario del request

    lista_aux = []
    for row in df_servicePoint.rdd.collect():
            dicc_aux = { "servicePointId": row.servicePointId }
            lista_aux.append(dicc_aux)
    dicc["servicePoint"] = lista_aux



    lista_aux = []
    for row in df_device.rdd.collect():
            dicc_aux = {
                            "brand": row.brand,
                            "model": row.model,
                            "deviceCategory": "METER",
                            "deviceId": row.deviceId,
                            "deviceDescription": row.deviceDescription,
                            "deviceStatus":row.deviceStatus
                            }
            lista_aux.append(dicc_aux)
    dicc["device"] = lista_aux


    lista_aux = []
    for row in df_relationDevice.rdd.collect():
            dicc_aux = {
                                    "servicePointId": row.servicePointId,
                                    "deviceId": row.deviceId,
                                    "meteringType": row.meteringType,
                                    "relationStartDate": row.relationStartDate,
                                    "relationEndDate": row.relationEndDate
                            }
            lista_aux.append(dicc_aux)
    dicc["relationDevice"] = lista_aux


    lista_aux = []
    for row in df_servicePointVariable_prof.rdd.collect():
            dicc_aux = {
                                    "readingType": row.readingType,
                                    "variableId": row.variableId,
                                    "servicePointId": row.servicePointId,
                                    "meteringType": row.meteringType,
                                    "unitOfMeasure": row.unitOfMeasure,
                                    "logNumber": row.logNumber,
                                    "channel": row.channel,
                                    "intervalSize": row.intervalSize
                            }
            lista_aux.append(dicc_aux)

    lista_aux_2 = []
    for row in df_servicePointVariable_regEvent.rdd.collect():
            dicc_aux = {
                                    "readingType": row.readingType,
                                    "variableId": row.variableId,
                                    "servicePointId": row.servicePointId,
                                    "meteringType": row.meteringType,
                                    "unitOfMeasure": row.unitOfMeasure
                            }
            lista_aux_2.append(dicc_aux)

    if lista_aux:
            lista_aux.extend(lista_aux_2)
            dicc["servicePointVariable"] = lista_aux
    else:
            dicc["servicePointVariable"] = lista_aux_2



    # Guardo el request como json. COMENTAR ESTA PARTE PARA LO DE GLUE
    #with open('enrich_request.json', 'w') as fp:
    #    json.dump(dicc, fp, indent=4)
    #print(df_.count() > 0)




    # BLOQUE DE ENRICH



    # levanto el archivo json de referencia para configuracion, sirve para probar en caso de que no funcione el endpoint
    #with open('respuesta_AMRDEF_sample_20200713.json') as json_file:
    #    req = json.load(json_file)
    #req = req["data"]

    # bloque para el caso en que el json sea una respuesta directa de la api (no generado manualmente)

    base_url = "http://80f56133-default-orchestra-c412-1531608832.us-east-1.elb.amazonaws.com/orchestrator/topology/set_up"
    
    headers = {"Content-Type":"application/json", "Transaction-Id":"test"
              , "User-Id":"test"}
    
    body = json.dumps(dicc)
    
    req =  request.Request(base_url, data=bytes(body.encode("utf-8"))
                            ,headers=headers,method="POST") # this will make the method "POST"
    resp = request.urlopen(req).read()
    response = json.loads(resp)
    req = response["data"]

    # Con el json levantado necesito extraer los datos para cruzar con el dataframe que tenemos
    # Extraigo los valores del diccionario en dos listas

    lista_relationDevice = [[item["servicePointId"],item["deviceId"],item["result"],item["relationStartDate"]]
                            for item in req["relationDevice"]]

    lista_servicePointVariable = [[item["readingType"],item["variableId"],item["servicePointId"],item["result"],
                                item["toStock"],item["lastReadDate"]]
                                for item in req["servicePointVariable"] ]

    # Creo un dataframe por lista

    Schema_relationDevice = StructType([StructField("servicePointId", StringType())\
                        ,StructField("deviceId", StringType())\
                        ,StructField("result_rD", BooleanType())\
                        ,StructField("relationStartDate", StringType())])

    df_relationDevice = spark.createDataFrame(lista_relationDevice,schema=Schema_relationDevice)



    Schema_servicePointVariable = StructType([StructField("readingType", StringType())\
                            ,StructField("variableId", StringType())\
                            ,StructField("servicePointId", StringType())\
                            ,StructField("result_sPV", BooleanType())\
                            ,StructField("toStock", BooleanType())\
                            ,StructField("lastReadDate", StringType())])
                            
    df_servicePointVariable = spark.createDataFrame(lista_servicePointVariable,schema=Schema_servicePointVariable)

    #IMPORTANTE 
    # elimino la columna relationStartDate porque en el paso siguiente hago el join con los valores enriquecidos
    # y la vuelvo a obtener (con valores actualizados)
    union = union.drop("relationStartDate")

    # hago un inner join de los dataframes creados con el dataframe original (enrichment)
    union = union.join(df_relationDevice, on=['servicePointId',"deviceId"], how='inner').coalesce(1)
    union = union.join(df_servicePointVariable, on=['variableId',"servicePointId","readingType"], how='inner').coalesce(1)


    # bloque calculo de readingUtcLocalTime
    # hay que tomar la hora readingLocalTime y pasarla a la hora correspondiente en Utc = 0 para cada registro del df.
    # para la conversion necesito tomar el valor de servicePointTimeZone

    # Si hay registros con valores nulos en servicePointTimeZone entonces no van a servir para el analisis y hay que eliminarlos
    union = union.filter(union.servicePointTimeZone.isNotNull())

    # defino el diccionario con las transformaciones para cada codigo
    utc_dicc = {
            '0': -12,
            '1': -11,
            '2': -10,
            '3': -9,
            '4': -8,
            '5': -7,
            '6': -7,
            '7': -6,
            '8': -6,
            '9': -6,
            '10': -5,
            '11': -5,
            '12': -5,
            '13': -4,
            '14': -4,
            '15': -3.5,
            '16': -3,
            '17': -3,
            '18': -2,
            '19': -1,
            '20': 0,
            '21': 0,
            '22': 1,
            '23': 1,
            '24': 1,
            '25': 2,
            '26': 2,
            '27': 2,
            '28': 2,
            '29': 2,
            '30': 3,
            '31': 3,
            '32': 3.5,
            '33': 4,
            '34': 4.5,
            '35': 5,
            '36': 5.5,
            '37': 6,
            '38': 7,
            '39': 8,
            '40': 8,
            '41': 9,
            '42': 9.5,
            '43': 9.5,
            '44': 10,
            '45': 10,
            '46': 10,
            '47': 11,
            '48': 12,
            '49': 12,
            '50': -8,
            '51': -7,
            '52': -6,
            '53': -6,
            '54': -6,
            '55': -6,
            '56': 10}

    def conversor_utc(row):
            if row.readingLocalTime:
                    fecha = datetime.strptime(row.readingLocalTime,'%Y-%m-%d %H:%M:%S')
                    fecha = fecha - timedelta(hours = utc_dicc[row.servicePointTimeZone])
                    return fecha.strftime('%Y-%m-%d %H:%M:%S')
            else:
                    pass

    # Aplico la funcion al dataframe
    udf_object = udf(conversor_utc, StringType())
    union = union.withColumn("readingUtcLocalTime", udf_object(struct([union[x] for x in union.columns])))


    # escribo los csv
    union.write.format('csv').mode("overwrite").save("./output/enriched", header="true", emptyValue="")


    return union


    #bloque tiempo
    dur = str(time.time() - start)
    dur = ".".join((dur.split(".")[0],dur.split(".")[1][:3]))

    message = guid +'-'+ hes + filename + ' | ' + "System" + ' | ' + owner + ' | '  + day + ' | ' + "Enrich job finished for file" + ' | ' + "INFO" + ' | '  + "Enrich" + ' | '  + dur + " seconds" + ' | ' + str(union.count()) + ' rows processed' + ' | ' + "Succesful"

    union.printSchema()
    """
    #este log sale
    log.info(message)

    #escribe todo en s3
    atexit.register(write_logs, body=log_stringio.getvalue(), bucket="test-charly-bucket-s3", key="logtest")

    quit()
    """