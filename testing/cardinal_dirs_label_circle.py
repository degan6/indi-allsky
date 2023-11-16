#!/usr/bin/env python3

#import sys
import argparse
from pathlib import Path
import math
import numpy
import cv2
#import PIL
from PIL import Image
import logging

logging.basicConfig(level=logging.INFO)
logger = logging


class CardinalDirsLabel(object):
    # label settings
    font_face = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.8
    font_thickness = 1
    line_color = (100, 200, 200)  # BGR
    line_type = cv2.LINE_AA


    def __init__(self):
        self._az = 0
        self._diameter = 1000

        self.top_offset = 20
        self.right_offset = 20
        self.bottom_offset = 5
        self.left_offset = 5


    @property
    def az(self):
        return self._az

    @az.setter
    def az(self, new_az):
        self._az = float(new_az)


    @property
    def diameter(self):
        return self._diameter

    @diameter.setter
    def diameter(self, new_diameter):
        self._diameter = int(new_diameter)


    def main(self, i, o):
        input_file = Path(i)
        output_file = Path(o)


        if not input_file.exists():
            raise Exception('Input file does not exist')

        if output_file.exists():
            raise Exception('Output file already exists')



        logger.info('Reading file: %s', input_file)

        with Image.open(str(input_file)) as img:
            image = cv2.cvtColor(numpy.array(img), cv2.COLOR_RGB2BGR)


        height, width = image.shape[:2]
        logger.info('Image: %d x %d', width, height)

        coord_dict = dict()
        # these return x, y lists
        coord_dict['N'] = self.findDirectionCoordinate(image, self.az)
        coord_dict['E'] = self.findDirectionCoordinate(image, self.az + 90)
        coord_dict['W'] = self.findDirectionCoordinate(image, self.az - 90)
        coord_dict['S'] = self.findDirectionCoordinate(image, self.az + 180)

        self.writeDirections(image, coord_dict)


        final_rgb = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        final_rgb.save(str(output_file), quality=90)


    def findDirectionCoordinate(self, image, dir_az):
        height, width = image.shape[:2]

        if dir_az >= 360:
            angle = dir_az - 360
        elif dir_az < 0:
            angle = dir_az + 360
        else:
            angle = dir_az

        logger.info('Finding direction angle for: %0.1f', angle)


        switch_angle = 90 - math.degrees(math.atan(height / width))
        logger.info('Switch angle: %0.1f', switch_angle)


        angle_180_r = abs(angle) % 180
        if angle_180_r > 90:
            angle_90_r = 90 - (abs(angle) % 90)
        else:
            angle_90_r = abs(angle) % 90


        if angle_90_r < switch_angle:
            hyp = self.diameter / 2
            c_angle = angle_90_r

            adj = math.cos(math.radians(c_angle)) * hyp

            if adj <= height / 2:
                opp = math.sin(math.radians(c_angle)) * hyp
            else:
                adj = height / 2
                opp = math.tan(math.radians(c_angle)) * adj
        else:
            hyp = self.diameter / 2
            c_angle = 90 - angle_90_r

            adj = math.cos(math.radians(c_angle)) * hyp

            if adj <= width / 2:
                opp = math.sin(math.radians(c_angle)) * hyp
            else:
                adj = width / 2
                opp = math.tan(math.radians(c_angle)) * adj


        logger.info('Opposite: %d', int(opp))
        logger.info('Adjacent: %d', int(adj))


        if angle >= 0 and angle < switch_angle:
            logger.info('Top right')
            d_x = (width / 2) + opp
            d_y = (height / 2) - adj
        elif angle >= switch_angle and angle < 90:
            logger.info('Right top')
            d_x = (width / 2) + adj
            d_y = (height / 2) - opp
        elif angle >= 90 and angle < (180 - switch_angle):
            logger.info('Right bottom')
            d_x = (width / 2) + adj
            d_y = (height / 2) + opp
        elif angle >= (180 - switch_angle) and angle < 180:
            logger.info('Bottom right')
            d_x = (width / 2) + opp
            d_y = (height / 2) + adj
        elif angle >= 180 and angle < (180 + switch_angle):
            logger.info('Bottom left')
            d_x = (width / 2) - opp
            d_y = (height / 2) + adj
        elif angle >= (180 + switch_angle) and angle < 270:
            logger.info('Left bottom')
            d_x = (width / 2) - adj
            d_y = (height / 2) + opp
        elif angle >= 270 and angle < (360 - switch_angle):
            logger.info('Left top')
            d_x = (width / 2) - adj
            d_y = (height / 2) - opp
        elif angle >= (360 - switch_angle) and angle < 360:
            logger.info('Top left')
            d_x = (width / 2) - opp
            d_y = (height / 2) - adj


        return int(d_x), int(d_y)


    def writeDirections(self, image, coord_dict):
        height, width = image.shape[:2]

        for k, v in coord_dict.items():
            x, y = v

            if x < self.left_offset:
                x = self.left_offset
            elif x > width - self.right_offset:
                x = width - self.right_offset

            if y < self.top_offset:
                y = self.top_offset
            elif y > height - self.bottom_offset:
                y = height - self.bottom_offset


            cv2.putText(
                img=image,
                text=k,
                org=(x, y),
                fontFace=self.font_face,
                color=(0, 0, 0),
                lineType=self.line_type,
                fontScale=self.font_scale,
                thickness=self.font_thickness + 1,
            )
            cv2.putText(
                img=image,
                text=k,
                org=(x, y),
                fontFace=self.font_face,
                color=self.line_color,
                lineType=self.line_type,
                fontScale=self.font_scale,
                thickness=self.font_thickness,
            )


if __name__ == "__main__":
    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        'input',
        help='Input file',
        type=str,
    )
    argparser.add_argument(
        '--output',
        '-o',
        help='output file',
        type=str,
        required=True,
    )
    argparser.add_argument(
        '--azimuth',
        '-a',
        help='azimuth [default: 45]',
        type=int,
        default=45,
    )
    argparser.add_argument(
        '--diameter',
        '-d',
        help='image circle diameter',
        type=int,
        required=True,
    )


    args = argparser.parse_args()

    dl = CardinalDirsLabel()
    dl.az = args.azimuth
    dl.diameter = args.diameter
    dl.main(args.input, args.output)
