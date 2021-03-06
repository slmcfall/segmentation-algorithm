#-------------------------------------------------------------------------------
# Name:        Segmentaion Algorithm
# Purpose:
#
# Author:      erabrahams and slmcfall
#
# Created:     05/04/2015
#-------------------------------------------------------------------------------

import arcpy, random, sys
arcpy.env.overwriteOutput = 1
arcpy.env.workspace = "in_memory"

# ---------- Parameters ---------- #


directory = arcpy.GetParameterAsText(0) + "\\"
parcelDict = {} # Master dictionary to hold parcel attributes key is ID = (pop, subsect)
errors = ""

centroidPoints = directory + "easm_segmentation_centroidPoints.shp"
centroids = directory + "easm_segmentation_centroids.shp"

#dissolve = directory + "easm_segmentation_dissolve.shp"
dissolve = "dissolve"

#parcels = directory + "easm_segmentation_parcels.shp"
parcels = "parcels_lyr"        # Layer for all parcels

#curPoint = directory + "easm_segmentation_curPoint.shp"
curPoint = "curPoint"

#distance = directory + "easm_segmentation_distance.dbf"
distance = "distance"

# --------- Small Helper Functions --------- #

# Calculate "far-ness"
def centroidPosition(x, y):
    return abs(x) + abs(y)

def mapInit(fileInput):
    arcpy.MakeFeatureLayer_management(fileInput, parcels)

# Intialize dictionary entries with (subsect = 0, population)
def dictInit(popfield):
    dictInit = arcpy.da.SearchCursor(parcels, ["FID", popfield])
    for parcel in dictInit:
        parcelDict[parcel[0]] = [0, parcel[1]]
    return parcelDict

def updateSubsections(subIter):
    subsectCursor = arcpy.da.UpdateCursor(parcels, ["FID", subIter])
    for parcel in subsectCursor:
        if parcel[1] == 0:
            parcel[1] = parcelDict[parcel[0]][0]
            subsectCursor.updateRow(parcel)

def resetSubsections(subIter):
    subsectCursor = arcpy.da.UpdateCursor(parcels, ["FID", subIter])
    for parcel in subsectCursor:
        if parcel[1] != 0:
            parcel[1] = 0
            subsectCursor.updateRow(parcel)

def getNextParcel(toDistance, boundlist):
    try:
        nextParcel = toDistance.pop(0)
        if nextParcel in boundlist:
            boundlist.remove(nextParcel)
        return nextParcel
    except IndexError:
        return None

# Find starting parceel for the next subsection. Picks closest bounding parcel
# to original starting parcel that is not yet in a subsection.
def findNextStart(toDistance, boundlist):
    for parcel in toDistance:
        ndx = parcel[1]
        if ndx in boundlist:
            boundlist.remove(ndx)
            if parcelDict[ndx][0] == 0:
                return ndx
    return None

def cleanupIntermediateFiles():
    arcpy.Delete_management(centroidPoints)
    arcpy.Delete_management(dissolve)
    arcpy.Delete_management(curPoint)
    arcpy.Delete_management(distance)

# --------- Workhorse Functions --------- #

def calculateCentroids(fileInput):
    #arcpy.AddMessage("Calculating centroids...\n")

    arcpy.FeatureToPoint_management(fileInput, centroidPoints, "CENTROID")
    arcpy.MakeFeatureLayer_management(centroidPoints, centroids)

# Create layers for parcels and boundline; find intersection
def getBounds(subIter):
    #arcpy.AddMessage("\t\tDetermining bound parcels...")

    updateSubsections(subIter)

    arcpy.SelectLayerByAttribute_management(parcels, "NEW_SELECTION", '"{Subsection}" = 0' .format(Subsection = subIter))
    arcpy.Dissolve_management(parcels, dissolve, "", "", "MULTI_PART", "DISSOLVE_LINES")
    arcpy.SelectLayerByLocation_management(parcels, "BOUNDARY_TOUCHES", dissolve, "", "NEW_SELECTION")

    boundlist = []
    boundary = arcpy.da.SearchCursor(parcels, "FID")
    for parcel in boundary:
        if parcelDict[parcel[0]][0] == 0:
            boundlist.append(parcel[0])

    arcpy.SelectLayerByAttribute_management(parcels, "CLEAR_SELECTION")
    return boundlist


# Iterate through boundary polygons to find the starting (farthest) parcel, and create a list of bound-poly indices.
def findStart(subIter):
    #arcpy.AddMessage("\tFinding starting parcel...")

    boundlist = getBounds(subIter)

    startNdx = random.randrange(len(boundlist))
    startID = boundlist[startNdx]

    return startID, boundlist

# Select starting centroid, and find distance to all other centroids.
def getDistanceToOthers(curNdx):
    #arcpy.AddMessage("\tCalculating distances...")

    arcpy.FeatureClassToFeatureClass_conversion(centroids, arcpy.env.workspace, curPoint, '"FID" = {ndx}'.format(ndx = curNdx))
    arcpy.PointDistance_analysis(curPoint, centroids, distance)

    toDistance = []
    dists = arcpy.da.SearchCursor(distance, ["NEAR_FID","DISTANCE"])

    for row in dists:
        if parcelDict[row[0]][0] == 0:
            toDistance.append([row[1], row[0]])

    toDistance.sort()
    return toDistance

def initializeDistrict(fileInput, popfield, totalSubsects):
    totalPop = 0
    totalTracts = 0

    popfile = arcpy.da.SearchCursor(fileInput, popfield)
    for parcel in popfile:
        totalPop += parcel[0]
        totalTracts += 1

    # Calculate T
    maxSubPop = totalPop/totalSubsects

    # Print totals and number of subsections
    arcpy.AddMessage("\nNumber of Children: " + str(totalPop))
    arcpy.AddMessage("Number of Tracts: " + str(totalTracts))
    arcpy.AddMessage("Number of Subsections: " + str(totalSubsects))
    arcpy.AddMessage("Max Subsection Population: " + str(maxSubPop))

    # Initialize the subsection loop.
    mapInit(fileInput)
    calculateCentroids(fileInput)

    return maxSubPop

def subdivideDistrict(fileInput, popfield, subsections, iterations, rngSeed):
    global errors
    errors = ""
    random.seed(rngSeed) # Initialize RNG seed with user input seed.

    maxSubPop = initializeDistrict(fileInput, popfield, subsections)
    for iteration in range(iterations):
        arcpy.AddMessage("\nCreating Iteration " + str(iteration + 1) + "...")
        makeDistrict(fileInput, popfield, subsections, maxSubPop, iteration)
    cleanupIntermediateFiles()

# ---------- Main function body ---------- #

def makeDistrict(fileInput, popfield, totalSubsects, maxSubPop, iteration):
    global errors
    badIter = False

    subField = "Sub_" + str(iteration + 1)
    arcpy.AddField_management(fileInput, subField, "SHORT")
    arcpy.CalculateField_management(fileInput, subField, '0')

    dictInit(popfield)
    boundlist = []

    currentSubsect = 1
    lastSubsectSmall = False
    while (currentSubsect < totalSubsects):
        arcpy.AddMessage("\tCreating subsection {num}..." .format(num = currentSubsect))

        # Initialize the next starting point.
        if len(boundlist) > 0:
            try:
                startNdx = findNextStart(toDistance, boundlist)
                toDistance = getDistanceToOthers(startNdx)

                curParcel = getNextParcel(toDistance, boundlist)
                curNdx = curParcel[1]
                curPop = parcelDict[curNdx][1]
            except RuntimeError:
                arcpy.AddMessage("Error finding starting parcel. Unable to create {s}." .format(s = str(subField)))
                addError("Error finding starting parcel. Unable to create {s}." .format(s = str(subField)), iteration, fileInput)
                badIter = True
                break

        # When current boundlist is exhausted, create a new inner bound.
        if len(boundlist) == 0:
            #arcpy.AddMessage("\tBoundlist empty, creating new boundline.")
            curNdx, boundlist = findStart(subField)
            curPop = parcelDict[curNdx][1]

            toDistance = getDistanceToOthers(curNdx)
            curParcel = findNextStart(toDistance, boundlist)  # Assign curParcel and remove start from lists.


        # Initialize loop
        subPop = 0
        #arcpy.AddMessage("\tAdding parcels...")

        # Populate nearby parcels with the current subsection ID.
        # First assign will be the starting parcel with a dist of 0.
        # lastSubsectSmall is a boolean check to alternate which subsections are "overpopulated" to maintain balance.
        while ((subPop + curPop) < maxSubPop) and curParcel is not None:

            parcelDict[curNdx][0] = currentSubsect
            subPop += curPop
            if curNdx in boundlist:
                boundlist.remove(curNdx)

            curParcel = getNextParcel(toDistance, boundlist)
            if curParcel is not None:
                curNdx = curParcel[1]
                curPop = parcelDict[curNdx][1]

        # OVERfill the current subsection if last one was smaller than the threshold.
        if lastSubsectSmall:
            while subPop < maxSubPop:
                parcelDict[curNdx][0] = currentSubsect
                subPop += curPop
                if curNdx in boundlist:
                    boundlist.remove(curNdx)

                curParcel = getNextParcel(toDistance, boundlist)
                if curParcel is not None:
                    curNdx = curParcel[1]
                    curPop = parcelDict[curNdx][1]

            lastSubsectSmall = False

        else:
            lastSubsectSmall = True

        #arcpy.AddMessage("Subsection {num} created.\n" .format(num = currentSubsect))
        arcpy.RefreshActiveView()
        currentSubsect += 1

    if badIter:
        resetSubsections(subField)

    else:
        # For last subsection, grab all parcels not yet assigned.
        arcpy.AddMessage("Creating subsection {num} by adding remaining parcels..." .format(num = currentSubsect))
        for parcel in parcelDict.values():
            if parcel[0] == 0:
                parcel[0] = currentSubsect

        #arcpy.AddMessage("Subsection {num} created.\n" .format(num = currentSubsect))
        updateSubsections(subField)
        #arcpy.RefreshActiveView()

# ---------- Error handling ---------- #

def addError(newError, iteration, fileInput):
    global errors
    if errors == "":
        filePath = fileInput.replace(".shp", "")
        errors += "Errors for " + filePath[-16:] + ":\n"
    errors += "    -" + newError + "\n"
    return errors

def getErrors():
    return errors

def main():
    fileInput = arcpy.GetParameterAsText(1)
    arcpy.SetParameterAsText(2, fileInput) # Changes are in-place, so output is the same file as input.
    pop_field = arcpy.GetParameterAsText(3)
    subsections = int(arcpy.GetParameterAsText(4))
    iterations = int(arcpy.GetParameterAsText(5))
    rngSeed = int(arcpy.GetParameterAsText(6))

    subdivideDistrict(fileInput, pop_field, subsections, iterations, rngSeed)

if __name__ =="__main__":
    main()



