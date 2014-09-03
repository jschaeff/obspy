from math import pi
import math
from decimal import *
from taupy.VelocityLayer import VelocityLayer
from taupy.SlownessLayer import SlownessLayer
from taupy.helper_classes import DepthRange, CriticalDepth, TimeDist


class SlownessModelError(Exception):
    pass


# noinspection PyPep8Naming
class SlownessModel(object):
    """This class provides storage and methods for generating slowness-depth pairs."""
    DEBUG = False
    DEFAULT_SLOWNESS_TOLERANCE = 1e-16
    radiusOfEarth = 6371.0

    # NB if the following are actually cleared (lists are mutable) every
    # time createSample is called, maybe it would be better to just put these
    # initialisations into the relevant methods? They do have to be persistent across
    # method calls in createSample though, so don't.

    # Stores the layer number for layers in the velocity model with a critical
    # point at their top. These form the "branches" of slowness sampling.
    criticalDepths = []  # will be list of CriticalDepth objects
    # Store depth ranges that contains a high slowness zone for P/S. Stored as
    # DepthRange objects, containing the top depth and bottom depth.
    highSlownessLayerDepthsP = []  # will be list of DepthRanges
    highSlownessLayerDepthsS = []
    # Stores depth ranges that are fluid, ie S velocity is zero. Stored as
    # DepthRange objects, containing the top depth and bottom depth.
    fluidLayerDepths = []

    # For methods that have an isPWave parameter
    SWAVE = False
    PWAVE = True

    def __init__(self, vMod, minDeltaP=0.1, maxDeltaP=11, maxDepthInterval=115, maxRangeInterval=2.5 * pi / 180,
                 maxInterpError=0.05, allowInnerCoreS=True, slowness_tolerance=DEFAULT_SLOWNESS_TOLERANCE, pLayers=[], sLayers=[]):

        self.vMod = vMod
        self.minDeltaP = minDeltaP
        self.maxDeltaP = maxDeltaP
        self.maxDepthInterval = maxDepthInterval
        self.maxRangeInterval = maxRangeInterval
        self.maxInterpError = maxInterpError
        self.allowInnerCoreS = allowInnerCoreS
        self.slowness_tolerance = slowness_tolerance
        ### This may be dodgy! Still not sure how the Java works!
        self.PLayers = pLayers
        self.SLayers = sLayers
        # It seems data is only put in here (and the longer constructor called) by the splitLayer method (and maybe
        # others it calls), so it seems reasonable to have an empty list as default for all instatiations of this class,
        # I suppose and hope it won't do any harm.
        self.createSample()

    def __str__(self):
        desc = "This is a dummy SlownessModel so there's nothing here really. Nothing to see. Move on."
        desc += "This might be interesting: slowness_tolerance ought to be 1e-16. It is:" + str(self.slowness_tolerance)
        return desc

    def createSample(self):
        """ This method takes a velocity model and creates a vector containing
        slowness-depth layers that, hopefully, adequately sample both slowness
        and depth so that the travel time as a function of distance can be
        reconstructed from the theta function."""
        # Some checks on the velocity model
        if self.vMod.validate() is False:
            raise SlownessModelError("Error in velocity model (vMod.validate failed)!")
        if self.vMod.getNumLayers() == 0:
            raise SlownessModelError("velModel.getNumLayers()==0")
        if self.vMod.layers[0].topSVelocity == 0:
            raise SlownessModelError(
                "Unable to handle zero S velocity layers at surface. "
                "This should be fixed at some point, but is a limitation of TauP at this point.")
        if self.DEBUG:
            print("start createSample")

        self.radiusOfEarth = self.vMod.radiusOfEarth

        if self.DEBUG: print("findCriticalPoints")
        self.findCriticalPoints()
        if self.DEBUG: print("coarseSample")
        self.coarseSample()
        if self.DEBUG and self.validate() is False:
            raise (SlownessModelError('validate failed after coarseSample'))
        if self.DEBUG: print("rayParamCheck")
        self.rayParamIncCheck()
        if self.DEBUG: print("depthIncCheck")
        self.depthIncCheck()
        if self.DEBUG: print("distanceCheck")
        self.distanceCheck()
        if self.DEBUG: print("fixCriticalPoints")
        self.fixCriticalPoints()

        if self.validate() is True:
            print("createSample seems to be done successfully.")
        else:
            raise SlownessModelError('SlownessModel.validate failed!')

    # noinspection PyCallByClass
    def findCriticalPoints(self):
        """ Finds all critical points within a velocity model.

         Critical points are first order discontinuities in
        velocity/slowness, local extrema in slowness. A high slowness
        zone is a low velocity zone, but it is possible to have a
        slight low velocity zone within a spherical earth that is not
        a high slowness zone and thus does not exhibit any of the
        pathological behavior of a low velocity zone.  """
        highSlownessZoneP = DepthRange()
        highSlownessZoneS = DepthRange()
        fluidZone = DepthRange()
        inFluidZone = False
        belowOuterCore = False
        inHighSlownessZoneP = False
        inHighSlownessZoneS = False
        # just some very big values (java had max possible of type, but these should do)
        minPSoFar = 1.1e300
        minSSoFar = 1.1e300
        # First remove any critical points previously stored
        # so these are effectively re-initialised... it's probably silly
        self.criticalDepths = []  # list of CriticalDepth
        self.highSlownessLayerDepthsP = []  # lists of DepthRange
        self.highSlownessLayerDepthsS = []
        self.fluidLayerDepths = []

        # Initialize the current velocity layer
        # to be zero thickness layer with values at the surface
        currVLayer = self.vMod.layers[0]
        currVLayer = VelocityLayer(0, currVLayer.topDepth, currVLayer.topDepth,
                                   currVLayer.topPVelocity, currVLayer.topPVelocity,
                                   currVLayer.topSVelocity, currVLayer.topSVelocity,
                                   currVLayer.topDensity, currVLayer.topDensity,
                                   currVLayer.topQp, currVLayer.topQp,
                                   currVLayer.topQs, currVLayer.topQs)
        currSLayer = SlownessLayer.create_from_vlayer(currVLayer, self.SWAVE)
        currPLayer = SlownessLayer.create_from_vlayer(currVLayer, self.PWAVE)
        # We know that the top is always a critical slowness so add 0
        self.criticalDepths.append(CriticalDepth(0, 0, 0, 0))
        # Check to see if starting in fluid zone.
        if inFluidZone is False and currVLayer.topSVelocity == 0:
            inFluidZone = True
            fluidZone = DepthRange(topDepth=currVLayer.topDepth)
            currSLayer = currPLayer
        if minSSoFar > currSLayer.topP:
            minSSoFar = currSLayer.topP
        # P is not a typo, it represents slowness, not P-wave speed.
        if minPSoFar > currPLayer.topP:
            minPSoFar = currPLayer.topP

        for layerNum, layer in enumerate(self.vMod.layers):
            prevVLayer = currVLayer
            prevSLayer = currSLayer
            prevPLayer = currPLayer
            # Could make the following a deep copy, but not necessary.
            # Also don't just replace layer here and in the loop
            # control with currVLayer, or the reference to the first
            # zero thickness layer that has been initialised above
            # will break.
            currVLayer = layer
            # Check again if in fluid zone
            if inFluidZone is False and currVLayer.topSVelocity == 0:
                inFluidZone = True
                fluidZone = DepthRange(topDepth=currVLayer.topDepth)
            # If already in fluid zone, check if exited (java line 909)
            if inFluidZone is True and currVLayer.topSVelocity != 0:
                if prevVLayer.botDepth > self.vMod.iocbDepth:
                    belowOuterCore = True
                inFluidZone = False
                fluidZone.botDepth = prevVLayer.botDepth
                self.fluidLayerDepths.append(fluidZone)

            currPLayer = SlownessLayer.create_from_vlayer(currVLayer, self.PWAVE)
            # If we are in a fluid zone ( S velocity = 0.0 ) or if we are below
            # the outer core and allowInnerCoreS=false then use the P velocity
            # structure to look for critical points.
            if inFluidZone or (belowOuterCore and self.allowInnerCoreS is False):
                currSLayer = currPLayer
            else:
                currSLayer = SlownessLayer.create_from_vlayer(currVLayer, self.SWAVE)

            if prevSLayer.botP != currSLayer.topP or prevPLayer.botP != currPLayer.topP:
                # a first order discontinuity
                self.criticalDepths.append(CriticalDepth(currSLayer.topDepth,
                                                         layerNum, -1, -1))
                if self.DEBUG:
                    print('First order discontinuity, depth =' + str(currSLayer.topDepth))
                    print('between' + str(prevPLayer), str(currPLayer))
                if inHighSlownessZoneS and currSLayer.topP < minSSoFar:
                    if self.DEBUG:
                        print("Top of current layer is the bottom"
                              + " of a high slowness zone.")
                    highSlownessZoneS.botDepth = currSLayer.topDepth
                    self.highSlownessLayerDepthsS.append(highSlownessZoneS)
                    inHighSlownessZoneS = False
                if inHighSlownessZoneP and currPLayer.topP < minPSoFar:
                    if self.DEBUG:
                        print("Top of current layer is the bottom"
                              + " of a high slowness zone.")
                    highSlownessZoneP.botDepth = currSLayer.topDepth
                    self.highSlownessLayerDepthsP.append(highSlownessZoneP)
                    inHighSlownessZoneP = False
                # Update minPSoFar and minSSoFar as all total reflections off
                # of the top of the discontinuity are ok even though below the
                # discontinuity could be the start of a high slowness zone.
                if minPSoFar > currPLayer.topP:
                    minPSoFar = currPLayer.topP
                if minSSoFar > currSLayer.topP:
                    minSSoFar = currSLayer.topP

                if inHighSlownessZoneS is False and (prevSLayer.botP < currSLayer.topP or
                                                     currSLayer.topP < currSLayer.botP):
                    # start of a high slowness zone S
                    if self.DEBUG:
                        print("Found S high slowness at first order "
                              + "discontinuity, layer = " + str(layerNum))
                    inHighSlownessZoneS = True
                    highSlownessZoneS = DepthRange(topDepth=currSLayer.topDepth)
                    highSlownessZoneS.rayParam = minSSoFar
                if inHighSlownessZoneP is False and (prevPLayer.botP < currPLayer.topP or
                                                     currPLayer.topP < currPLayer.botP):
                    # start of a high slowness zone P
                    if self.DEBUG:
                        print("Found P high slowness at first order "
                              + "discontinuity, layer = " + str(layerNum))
                    inHighSlownessZoneP = True
                    highSlownessZoneP = DepthRange(topDepth=currPLayer.topDepth)
                    highSlownessZoneP.rayParam = minPSoFar

            elif ((prevSLayer.topP - prevSLayer.botP) *
                  (prevSLayer.botP - currSLayer.botP) < 0) or (
                      (prevPLayer.topP - prevPLayer.botP) *
                      (prevPLayer.botP - currPLayer.botP)) < 0:
                # local slowness extrema, java l 1005
                self.criticalDepths.append(CriticalDepth(currSLayer.topDepth, layerNum,
                                                         -1, -1))
                if self.DEBUG:
                    print("local slowness extrema, depth=" + str(currSLayer.topDepth))
                # here is line 1014 of the java src!
                if inHighSlownessZoneP is False and currPLayer.topP < currPLayer.botP:
                    if self.DEBUG:
                        print("start of a P high slowness zone, local slowness extrema,"
                              + "minPSoFar= " + str(minPSoFar))
                    inHighSlownessZoneP = True
                    highSlownessZoneP = DepthRange(topDepth=currPLayer.topDepth)
                    highSlownessZoneP.rayParam = minPSoFar
                if inHighSlownessZoneS is False and currSLayer.topP < currSLayer.botP:
                    if self.DEBUG:
                        print("start of a S high slowness zone, local slowness extrema,"
                              + "minSSoFar= " + str(minSSoFar))
                    inHighSlownessZoneS = True
                    highSlownessZoneS = DepthRange(topDepth=currSLayer.topDepth)
                    highSlownessZoneS.rayParam = minSSoFar

            if inHighSlownessZoneP and currPLayer.botP < minPSoFar:
                # P: layer contains the bottom of a high slowness zone. java l 1043
                if self.DEBUG:
                    print("layer contains the bottom of a P "
                          + "high slowness zone. minPSoFar=" + str(minPSoFar), currPLayer)
                highSlownessZoneP.botDepth = self.findDepth(minPSoFar, layerNum,
                                                            layerNum, self.PWAVE)
                self.highSlownessLayerDepthsP.append(highSlownessZoneP)
                inHighSlownessZoneP = False

            if inHighSlownessZoneS and currSLayer.botP < minSSoFar:
                # S: layer contains the bottom of a high slowness zone. java l 1043
                if self.DEBUG:
                    print("layer contains the bottom of a S "
                          + "high slowness zone. minSSoFar=" + str(minSSoFar), currSLayer)
                # in fluid layers we want to check PWAVE structure
                # when looking for S wave critical points
                porS = (self.PWAVE if currSLayer == currPLayer else self.SWAVE)
                highSlownessZoneS.botDepth = self.findDepth(minSSoFar, layerNum,
                                                            layerNum, porS)
                self.highSlownessLayerDepthsS.append(highSlownessZoneS)
                inHighSlownessZoneS = False
            if minPSoFar > currPLayer.botP:
                minPSoFar = currPLayer.botP
            if minPSoFar > currPLayer.topP:
                minPSoFar = currPLayer.topP
            if minSSoFar > currSLayer.botP:
                minSSoFar = currSLayer.botP
            if minSSoFar > currSLayer.topP:
                minSSoFar = currSLayer.topP
            if self.DEBUG and inHighSlownessZoneS:
                print("In S high slowness zone, layerNum = " + str(layerNum)
                      + " minSSoFar=" + str(minSSoFar))
            if self.DEBUG and inHighSlownessZoneP:
                print("In P high slowness zone, layerNum = " + str(layerNum)
                      + " minPSoFar=" + str(minPSoFar))

        # We know that the bottommost depth is always a critical slowness,
        # so we add vMod.getNumLayers()
        # java line 1094
        self.criticalDepths.append(CriticalDepth(self.radiusOfEarth,
                                                 self.vMod.getNumLayers(), -1, -1))

        # Check if the bottommost depth is contained within a high slowness
        # zone, might happen in a flat non-whole-earth model
        if inHighSlownessZoneS:
            highSlownessZoneS.botDepth = currVLayer.botDepth
            self.highSlownessLayerDepthsS.append(highSlownessZoneS)
        if inHighSlownessZoneP:
            highSlownessZoneP.botDepth = currVLayer.botDepth
            self.highSlownessLayerDepthsP.append(highSlownessZoneP)

        # Check if the bottommost depth is contained within a fluid zone, this
        # would be the case if we have a non whole earth model with the bottom
        # in the outer core or if allowInnerCoreS == false and we want to use
        # the P velocity structure in the inner core.
        if inFluidZone:
            fluidZone.botDepth = currVLayer.botDepth
            self.fluidLayerDepths.append(fluidZone)

        # optionally implement later: print all critical vel layers in debug mode

        if self.validate() is False:
            raise SlownessModelError("Validation failed after findDepth")

    def getNumLayers(self, isPWave):
        """This is meant to return the number of pLayers and sLayers.
        I have not yet been able to find out how these are known in
        the java code."""

        # Where
        # self.PLayers = pLayers
        # and the pLayers have been provided in the constructor, but I
        # don't understand from where!
        if isPWave:
            return len(self.PLayers)
        else:
            return len(self.SLayers)

    def findDepth_from_depths(self, rayParam, topDepth, botDepth, isPWave):
        """Finds a depth corresponding to a slowness between two given depths in the
        Velocity Model by calling findDepth with layer numbers."""
        topLayerNum = self.vMod.layerNumberBelow(topDepth)
        if self.vMod.layers[topLayerNum].botDepth == topDepth:
            topLayerNum += 1
        botLayerNum = self.vMod.layerNumberAbove(botDepth)
        return self.findDepth(rayParam, topLayerNum, botLayerNum, isPWave)

    def findDepth(self, p, topCriticalLayer, botCriticalLayer, isPWave):
        """Finds a depth corresponding to a slowness p (here defined as (6731-depth) / velocity ,
        and sometimes called ray parameter)  between two given velocity
        layers, including the top and the bottom. We also check to see if the
        slowness is less than the bottom slowness of these layers but greater
        than the top slowness of the next deeper layer. This corresponds to a
        total reflection. In this case a check needs to be made to see if this is
        an S wave reflecting off of a fluid layer, use P velocity below in this
        case. We assume that slowness is monotonic within these layers and
        therefore there is only one depth with the given slowness. This means we
        return the first depth that we find.

         SlownessModelError occurs if topCriticalLayer > botCriticalLayer because
                   there are no layers to search, or if there is an increase
                   in slowness, ie a negative velocity gradient, that just
                   balances the decrease in slowness due to the spherical
                   earth, or if the ray parameter p is not contained within
                   the specified layer range."""

        #topP = 1.1e300  # dummy numbers
        #botP = 1.1e300
        waveType = 'P' if isPWave else 'S'

        if topCriticalLayer > botCriticalLayer:
            raise SlownessModelError("findDepth: no layers to search (wrong layer num?)")
        for layerNum in range(topCriticalLayer, botCriticalLayer + 1):
            velLayer = self.vMod.layers[layerNum]
            topVelocity = velLayer.evaluateAtTop(waveType)
            botVelocity = velLayer.evaluateAtBottom(waveType)
            topP = self.toSlowness(topVelocity, velLayer.topDepth)
            botP = self.toSlowness(botVelocity, velLayer.botDepth)
            # check to see if we are within 'chatter level' (numerical error) of the top or
            # bottom and if so then return that depth.
            if abs(topP - p) < self.slowness_tolerance:
                return velLayer.topDepth
            if abs(p - botP) < self.slowness_tolerance:
                return velLayer.botDepth

            if (topP - p) * (p - botP) >= 0:
                # Found layer containing p.
                # We interpolate assuming that velocity is linear within
                # this interval. So slope is the slope for velocity versus depth
                slope = (botVelocity - topVelocity) / (velLayer.botDepth - velLayer.topDepth)
                depth = self.interpolate(p, topVelocity, velLayer.topDepth, slope)
                return depth
            elif layerNum == topCriticalLayer and abs(p - topP) < self.slowness_tolerance:
                # Check to see if p is just outside the topmost layer. If so
                # then return the top depth.
                return velLayer.topDepth

            # Is p a total reflection? botP is the slowness at the bottom
            # of the last velocity layer from the previous loop, set topP
            # to be the slowness at the top of the next layer.
            if layerNum < self.vMod.getNumLayers() - 1:
                velLayer = self.vMod.layers[layerNum + 1]
                topVelocity = velLayer.evaluateAtTop(waveType)
                if isPWave is False and self.depthInFluid(velLayer.topDepth):
                    # Special case for S waves above a fluid. If top next
                    # layer is in a fluid then we should set topVelocity to
                    # be the P velocity at the top of the layer.
                    topVelocity = velLayer.evaluateAtTop('P')

                topP = self.toSlowness(topVelocity, velLayer.topDepth)
                if botP >= p >= topP:
                    return velLayer.topDepth

        # noinspection PyUnboundLocalVariable
        if abs(p - botP) < self.slowness_tolerance:
            # java line 1275
            #Check to see if p is just outside the bottommost layer. If so
            #than return the bottom depth.
            print(" p is just outside the bottommost layer. This probably shouldn't be allowed to happen!")
            # noinspection PyUnboundLocalVariable
            return velLayer.getBotDepth()

        raise SlownessModelError("slowness p=" + str(p) + "is not contained within the specified layers."
                                 + " topCriticalLayer=" + str(topCriticalLayer)
                                 + " botCriticalLayer=" + str(botCriticalLayer))

    def toSlowness(self, velocity, depth):
        if velocity == 0:
            raise SlownessModelError("toSlowness: velocity can't be zero, at depth" +
                                     str(depth),
                                     "Maybe related to using S velocities in outer core?")
        return (self.radiusOfEarth - depth) / velocity

    def interpolate(self, p, topVelocity, topDepth, slope):
        denominator = p * slope + 1
        if denominator == 0:
            raise SlownessModelError("Negative velocity gradient that just balances the slowness gradient "
                                     "of the spherical slowness, i.e. Earth flattening. Instructions unclear; explode.")
        else:
            depth = (self.radiusOfEarth + p * (topDepth * slope - topVelocity)) / denominator
            return depth

    def depthInFluid(self, depth):
        """ Determines if the given depth is contained within a fluid zone. The fluid
        zone includes its upper boundary but not its lower boundary. The top and
        bottom of the fluid zone are not returned as a DepthRange, just like in the java code,
        despite its claims to the contrary."""
        for elem in self.fluidLayerDepths:
            if elem.topDepth <= depth < elem.botDepth:
                return True
        return False

    def coarseSample(self):
        self.PLayers = []
        self.SLayers = []
        # to initialise prevVLayer
        origVLayer = self.vMod.layers[0]
        origVLayer = VelocityLayer(0, origVLayer.topDepth, origVLayer.topDepth, origVLayer.topPVelocity,
                                   origVLayer.topPVelocity, origVLayer.topSVelocity, origVLayer.topSVelocity,
                                   origVLayer.topDensity, origVLayer.topDensity,
                                   origVLayer.topQp, origVLayer.topQp, origVLayer.topQs, origVLayer.topQs)
        for layer in self.vMod.layers:
            prevVLayer = origVLayer
            origVLayer = layer
            # Check for first order discontinuity. However, we only
            # consider S discontinuities in the inner core if
            # allowInnerCoreS is true.
            if prevVLayer.botPVelocity != origVLayer.topPVelocity or(
                prevVLayer.botSVelocity != origVLayer.topSVelocity and
                (self.allowInnerCoreS or origVLayer.topDepth < self.vMod.iocbDepth)):
                # If we are going from a fluid to a solid or solid to
                # fluid, ex core mantle or outer core to inner core then we
                # need to use the P velocity for determining the S
                # discontinuity.
                if prevVLayer.botSVelocity == 0:
                    topSVel = prevVLayer.botPVelocity
                else:
                    topSVel = prevVLayer.botSVelocity
                if origVLayer.topSVelocity == 0:
                    botSVel = origVLayer.topPVelocity
                else:
                    botSVel = origVLayer.topSVelocity
                # Add the zero thickness, but with nonzero slowness step,
                # layer corresponding to the discontinuity.
                currVLayer = VelocityLayer(layer.layer_number, prevVLayer.botDepth, prevVLayer.botDepth,
                                           prevVLayer.botPVelocity, origVLayer.topPVelocity,
                                           topSVel, botSVel)
                currPLayer = SlownessLayer.create_from_vlayer(currVLayer, self.PWAVE)
                self.PLayers.append(currPLayer)
                if (prevVLayer.botSVelocity == 0
                    and origVLayer.topSVelocity == 0) or (self.allowInnerCoreS is False
                                                          and currVLayer.topDepth >= self.vMod.iocbDepth):
                    currSLayer = currPLayer
                else:
                    currSLayer = SlownessLayer.create_from_vlayer(currVLayer, self.SWAVE)
                self.SLayers.append(currSLayer)
            currPLayer = SlownessLayer.create_from_vlayer(origVLayer, self.PWAVE)
            self.PLayers.append(currPLayer)
            if self.depthInFluid(origVLayer.topDepth) or (self.allowInnerCoreS is False
                                                          and origVLayer.topDepth >= self.vMod.iocbDepth):
                currSLayer = currPLayer
            else:
                currSLayer = SlownessLayer.create_from_vlayer(origVLayer, self.SWAVE)
            self.SLayers.append(currSLayer)
        # Make sure that all high slowness layers are sampled exactly
        # at their bottom
        for highZone in self.highSlownessLayerDepthsS:
            sLayerNum = self.layerNumberAbove(highZone.botDepth, self.SWAVE)
            highSLayer = self.SLayers[sLayerNum]
            while highSLayer.topDepth == highSLayer.botDepth and ((highSLayer.topP - highZone.rayParam)
                                                                  * (highZone.rayParam - highSLayer.botP) < 0):
                sLayerNum += 1
                highSLayer = self.SLayers[sLayerNum]
            if highZone.rayParam != highSLayer.botP:
                self.addSlowness(highZone.rayParam, self.SWAVE)
        for highZone in self.highSlownessLayerDepthsP:
            sLayerNum = self.layerNumberAbove(highZone.botDepth, self.PWAVE)
            highSLayer = self.PLayers[sLayerNum]
            while highSLayer.topDepth == highSLayer.botDepth and ((highSLayer.topP - highZone.rayParam)
                                                                  * (highZone.rayParam - highSLayer.botP) < 0):
                sLayerNum += 1
                highSLayer = self.PLayers[sLayerNum]
            if highZone.rayParam != highSLayer.botP:
                self.addSlowness(highZone.rayParam, self.PWAVE)
        # Make sure P and S are consistent
        botP = -1
        for layer in self.PLayers:
            topP = layer.topP
            if topP != botP:
                self.addSlowness(topP, self.SWAVE)
            botP = layer.botP
            self.addSlowness(botP, self.SWAVE)
        botP = -1
        for layer in self.SLayers:
            topP = layer.topP
            if topP != botP:
                self.addSlowness(topP, self.PWAVE)
            botP = layer.botP
            self.addSlowness(botP, self.SWAVE)

    def layerNumberAbove(self, depth, isPWave):
        """Finds the index of the slowness layer that contains the given depth. Note
        that if the depth is a layer boundary, it returns the shallower of the
        two or possibly more (since total reflections are zero thickness layers)
        layers.
        Error occurs if no layer in the slowness model contains the given depth."""
        foundLayerNum = self.layerNumForDepth(depth, isPWave)
        tempLayer = self.getSlownessLayer(foundLayerNum, isPWave)
        # Check if given depth is on a boundary.
        while tempLayer.topDepth == depth and foundLayerNum > 0:
            foundLayerNum -= 1
            tempLayer = self.getSlownessLayer(foundLayerNum, isPWave)
        return foundLayerNum

    def layerNumForDepth(self, depth, isPWave):
        if isPWave:
            layers = self.PLayers
        else:
            layers = self.SLayers
        # check to make sure depth is within the range available
        if depth < layers[0].topDepth or depth > layers[-1].botDepth:
            raise SlownessModelError("No layer contains this depth")
        tooSmallNum = 0
        tooLargeNum = len(layers) - 1
        while True:
            if tooLargeNum - tooSmallNum < 3:
                #  "end of Newton, just check" (what?)
                currentNum = tooSmallNum
                while currentNum <= tooLargeNum:
                    tempLayer = self.getSlownessLayer(currentNum, isPWave)
                    if tempLayer.topDepth <= depth <= tempLayer.botDepth:
                        return currentNum
                    currentNum += 1
            else:
                currentNum = int(Decimal((tooSmallNum + tooLargeNum) / 2.0).to_integral_value(ROUND_HALF_EVEN))
            tempLayer = self.getSlownessLayer(currentNum, isPWave)
            if tempLayer.topDepth > depth:
                tooLargeNum = currentNum - 1
            elif tempLayer.botDepth < depth:
                tooSmallNum = currentNum + 1
            else:
                return currentNum
            if tooSmallNum > tooLargeNum:
                raise ArithmeticError("tooSmallNum: " + str(tooSmallNum) + " >= tooLargeNum: " + str(tooLargeNum))

    def getSlownessLayer(self, layerNum, isPWave):
        if isPWave:
            return self.PLayers[layerNum]
        else:
            return self.SLayers[layerNum]

    def addSlowness(self, p, isPWave):
        """Adds the given ray parameter, p, to the slowness sampling for the given
        waveType. It splits slowness layers as needed and keeps P and S sampling
        consistent within fluid layers. Note, this makes use of the velocity
        model, so all interpolation is linear in velocity, not in slowness!"""
        if isPWave:
            # NB Just like Java (fortunately) these are shallow copies -- values are modified in place!
            layers = self.PLayers
            otherLayers = self.SLayers
        else:
            layers = self.SLayers
            otherLayers = self.PLayers
        for i, sLayer in enumerate(layers):
            if sLayer.topDepth != sLayer.botDepth:
                topVelocity = self.vMod.evaluateBelow(sLayer.topDepth, 'P' if isPWave else 'S')
                botVelocity = self.vMod.evaluateAbove(sLayer.botDepth, 'P' if isPWave else 'S')
            else:
                # If depths are the same only need topVelocity, and just
                # to verify we are not in a fluid
                topVelocity = self.vMod.evaluateAbove(sLayer.botDepth, 'P' if isPWave else 'S')
                botVelocity = self.vMod.evaluateBelow(sLayer.topDepth, 'P' if isPWave else 'S')
            # Don't need to check for S waves in a fluid or in inner core if
            # allowInnerCoreS is False.
            if not isPWave:
                if self.allowInnerCoreS is False and sLayer.botDepth > self.vMod.iocbDepth:
                    break
                elif topVelocity == 0:
                    continue
            if (sLayer.topP - p) * (p - sLayer.botP) > 0:
                botDepth = sLayer.botDepth
                if sLayer.botDepth != sLayer.topDepth:
                    # Not a zero thickness layer, so calculate the depth for the ray parameter.
                    slope = (botVelocity - topVelocity) / (sLayer.botDepth - sLayer.topDepth)
                    botDepth = self.interpolate(p, topVelocity, sLayer.topDepth, slope)
                botLayer = SlownessLayer(p, botDepth, sLayer.botP, sLayer.botDepth)
                topLayer = SlownessLayer(sLayer.topP, sLayer.topDepth, p, botDepth)
                # The list operations here should really be correct, after painstakingly working through Java
                # and Python documentations and trying the behaviour of both.
                layers.pop(i)
                layers.insert(i, botLayer)
                layers.insert(i, topLayer)
                # To mimic the Java behaviour of returning -1 when item not in list.
                try:
                    otherIndex = otherLayers.index(sLayer)
                except ValueError:
                    otherIndex = -1
                if otherIndex != -1:
                    otherLayers.pop(otherIndex)
                    otherLayers.insert(otherIndex, botLayer)
                    otherLayers.insert(otherIndex, topLayer)

    def rayParamIncCheck(self):
        """Checks to make sure that no slowness layer spans more than maxDeltaP."""
        for layers in [self.SLayers, self.PLayers]:
            for sLayer in layers:
                if abs(sLayer.topP - sLayer.botP) > self.maxDeltaP:
                    numNewP = math.ceil(abs(sLayer.topP - sLayer.botP) / self.maxDeltaP)
                    deltaP = (sLayer.topP - sLayer.botP) / numNewP
                    rayNum = 1
                    while rayNum < numNewP:
                        self.addSlowness(sLayer.topP + rayNum * deltaP, self.PWAVE)
                        self.addSlowness(sLayer.topP + rayNum * deltaP, self.SWAVE)
                        rayNum += 1

    def depthIncCheck(self):
        """Checks to make sure no slowness layer spans more than maxDepthInterval."""
        for which_codeblock, layers in enumerate([self.SLayers, self.PLayers]):
            for sLayer in layers:
                if (sLayer.botDepth - sLayer.topDepth) > self.maxDepthInterval:
                    newNumDepths = math.ceil((sLayer.botDepth - sLayer.topDepth) / self.maxDepthInterval)
                    deltaDepth = (sLayer.botDepth - sLayer.topDepth) / newNumDepths
                    depthNum = 1
                    while depthNum < newNumDepths:
                        # Could do if layers == self.SLayers, but that would be a bit heavy.
                        # In fact, any comparison here might be quite slow, I have a feeling this runs a lot... ?
                        if which_codeblock == 0:
                            velocity = self.vMod.evaluateAbove(sLayer.topDepth + depthNum * deltaDepth, 'S')
                            if velocity == 0 or (self.allowInnerCoreS is False and sLayer.topDepth
                                                 + depthNum * deltaDepth >= self.vMod.iocbDepth):
                                velocity = self.vMod.evaluateAbove(sLayer.topDepth + depthNum * deltaDepth, 'P')
                            p = self.toSlowness(velocity, sLayer.topDepth + depthNum * deltaDepth)
                        else:
                            p = self.toSlowness(self.vMod.evaluateAbove(sLayer.topDepth + depthNum * deltaDepth, 'P'),
                                                sLayer.topDepth + depthNum * deltaDepth)
                        self.addSlowness(p, self.PWAVE)
                        self.addSlowness(p, self.SWAVE)
                        depthNum += 1

    def distanceCheck(self):
        """Checks to make sure no slowness layer spans more than maxRangeInterval
        and that the (estimated) error due to linear interpolation is less than
        maxInterpError.
        """
        for currWaveType in [self.SWAVE, self.PWAVE]:
            isCurrOK = False
            isPrevOK = False
            j = 0
            sLayer = self.getSlownessLayer(j, currWaveType)
            while j < self.getNumLayers(currWaveType):
                prevSLayer = sLayer
                sLayer = self.getSlownessLayer(j, currWaveType)
                if (self.depthInHighSlowness(sLayer.botDepth, sLayer.botP, currWaveType) is False
                     and self.depthInHighSlowness(sLayer.topDepth, sLayer.topP,currWaveType) is False):
                    # Don't calculate prevTD if we can avoid it
                    if isCurrOK:
                        if isPrevOK:
                            prevPrevTD = prevTD
                        else:
                            prevPrevTD = None
                        prevTD = currTD
                        isPrevOK = True
                    else:
                        prevTD = self.approxDistance(j - 1, sLayer.topP, currWaveType)
                        isPrevOK = True
                    currTD = self.approxDistance(j, sLayer.botP, currWaveType)
                    isCurrOK = True
                    # Check for jump of too great distance
                    if (abs(prevTD.distRadian - currTD.distRadian) > self.maxRangeInterval and
                        abs(sLayer.topP - sLayer.botP) > 2 * self.minDeltaP):
                        if self.DEBUG:
                            print("At "+str(j)+" Distance jump too great (>maxRangeInterval "+str(self.maxRangeInterval)
                                               + "), adding slowness. ")
                        self.addSlowness((sLayer.topP + sLayer.botP) / 2, self.PWAVE)
                        self.addSlowness((sLayer.topP + sLayer.botP) / 2, self.SWAVE)
                        currTD = prevTD
                        prevTD = prevPrevTD
                    else:
                        # Make guess as to error estimate due to linear interpolation if it is not ok, then we split
                        # both the previous and current slowness layers, this has the nice, if unintended, consequence
                        # of adding extra samples in the neighborhood of poorly sampled caustics.
                        splitRayParam = (sLayer.topP + sLayer.botP) / 2
                        allButLayer = self.approxDistance(j-1, splitRayParam, currWaveType)
                        splitLayer = SlownessLayer(sLayer.topP, sLayer.topDepth, splitRayParam,
                                                   sLayer.bullenDepthFor(splitRayParam, self.radiusOfEarth))
                        justLayer = splitLayer.bullenRadialSlowness(splitRayParam, self.radiusOfEarth)
                        splitTD = TimeDist(splitRayParam, allButLayer.time + 2*justLayer.time,
                                           allButLayer.distRadian + 2*justLayer.distRadian)
                        if (abs(currTD.time - ((splitTD.time - prevTD.time) * (currTD.distRadian - prevTD.distRadian)
                              / (splitTD.distRadian - prevTD.distRadian) + prevTD.time)) > self.maxInterpError):
                            self.addSlowness((prevSLayer.topP + prevSLayer. botP) / 2, self.PWAVE)
                            self.addSlowness((prevSLayer.topP + prevSLayer. botP) / 2, self.SWAVE)
                            self.addSlowness((sLayer.topP + sLayer.botP) / 2, self.PWAVE)
                            self.addSlowness((sLayer.topP + sLayer.botP) / 2, self.SWAVE)
                            currTD = prevPrevTD
                            isPrevOK = False
                            if j > 0:
                                # Back up one step unless we are at the beginning, then stay put.
                                j -= 1
                                sLayer = self.getSlownessLayer(j-1 if j-1 >= 0 else 0, currWaveType)
                                # This sLayer will become prevSLayer in the next loop.
                            else:
                                isPrevOK = False
                                isCurrOK = False
                        else:
                            j += 1
                            if self.DEBUG and j % 10 == 0:
                                print(j)
                else:
                    prevPrevTD = None
                    prevTD = None
                    isCurrOK = False
                    isPrevOK = False
                    j += 1
                    if self.DEBUG and j % 100 == 0:
                        print(j)
            if self.DEBUG:
                print("Number of " + ("P" if currWaveType else "S") + " slowness layers: " + str(j))

    def depthInHighSlowness(self, depth, rayParam, isPWave):
        """Determines if the given depth and corresponding slowness is contained
        within a high slowness zone. Whether the high slowness zone includes its
        upper boundary and its lower boundaries depends upon the ray parameter.
        The slowness at the depth is needed because if depth happens to
        correspond to a discontinuity that marks the bottom of the high slowness
        zone but the ray is actually a total reflection then it is not part of
        the high slowness zone. The ray parameter that delimits the zone, ie it
        can turn at the top and the bottom, is in the zone at the top, but out of
        the zone at the bottom. (?)
        NOTE: I changed this method a bit by throwing out some seemingly useless copying
        of the values in tempRange, which I think are not used anywhere else."""
        if isPWave:
            highSlownessLayerDepths = self.highSlownessLayerDepthsP
        else:
            highSlownessLayerDepths = self.highSlownessLayerDepthsS
        for tempRange in highSlownessLayerDepths:
            if tempRange.topDepth <= depth <= tempRange.botDepth:
                if rayParam > tempRange.rayParam or (rayParam == tempRange.rayParam and depth == tempRange.topDepth):
                    return True
        return False

    def approxDistance(self, slownessTurnLayer, p, isPWave):
        """Generates approximate distance, in radians, for a ray from a surface
        source that turns at the bottom of the given slowness layer."""
        # First, if the slowness model contains less than slownessTurnLayer elements
        # we can't calculate a distance.
        if slownessTurnLayer >= self.getNumLayers(isPWave):
            raise SlownessModelError("Can't calculate a distance when getNumLayers() is smaller than"
                                     "the given slownessTurnLayer!")
        if p < 0:
            raise SlownessModelError("Ray parameter must not be negative!")
        td = TimeDist(p)
        for layerNum in range(0, slownessTurnLayer + 1):
            td.add(self.layerTimeDist(p, layerNum, isPWave))
        # Return 2* distance and time because there is a downgoing as well as an
        # upgoing leg, which are equal since this is for a surface source.
        td.distRadian *= 2
        td.time *= 2
        return td

    def layerTimeDist(self, sphericalRayParam, layerNum, isPWave):
        # Calculates the time and distance increments accumulated by a ray of
        # spherical ray parameter p when passing through layer layerNum.
        # Note that this gives 1/2 of the true range and time
        # increments since there will be both an upgoing and a downgoing path.
        # Only does the calculation for the simple cases of the centre of the Earth, where the ray parameter is zero,
        # or for constant velocity layers. Else, it calls SlownessLayer.bullenRadialSlowness.
        # Error occurs if the ray with the given spherical ray parameter
        # cannot propagate within this layer, or if the ray turns within this layer but not at the bottom.
        timeDist = TimeDist(sphericalRayParam)


    def fixCriticalPoints(self):
        pass

    def validate(self):
        return True
